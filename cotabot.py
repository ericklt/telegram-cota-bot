import logging
import pickle
import time
from threading import Thread
from functools import wraps

from telegram import utils
from telegram import (ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardButton,
                      ReplyKeyboardRemove, InlineKeyboardMarkup,
                      ChatAction, ParseMode)
from telegram.ext import (Updater, CommandHandler, CallbackQueryHandler,
                          MessageHandler, Filters, 
                          RegexHandler, ConversationHandler)

# Enable logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                    level=logging.INFO)

logger = logging.getLogger(__name__)

def send_typing_action(func):
    
    @wraps(func)
    def command_func(bot, update, *args, **kwargs):
        bot.send_chat_action(chat_id=update.effective_message.chat_id, action=ChatAction.TYPING)
        return func(bot, update, *args, **kwargs)
        
    return command_func

class CotaParticipant:
    def __init__(self, _id, first_name, last_name):
        self._id = _id
        self.first_name = first_name
        self.last_name = last_name
        self.payed = False

class Cota:
    def __init__(self, _id, creator_id, name=None, value=None):
        self._id = _id
        self.creator_id = creator_id
        self.name = name
        self.value = value
        self.going = {}

    def set_value(self, value):
        try:
            self.value = float(value.replace(',', '.'))
        except:
            self.value = None

    def add_participant(self, _id, first_name, last_name):
        self.going[_id] = CotaParticipant(_id, first_name, last_name)

    def remove_participant(self, _id):
        del self.going[_id]
        
class CotaButtonView:
    def __init__(self, cota):
        self.cota = cota
        
    def btn(self):
        val = '' if not self.cota.value else ' - R$ {:.02f}'.format(self.cota.value)
        btn_text = '[{}] {}'.format(len(self.cota.going), self.cota.name) + val
        return InlineKeyboardButton(btn_text, callback_data='show_cota {}'.format(self.cota._id))


# All possible Interactive Boxes States

class MainListState:
    def __init__(self, iBox):
        self.iBox = iBox
        
    def update(self, bot):
        header = 'Lista de Cotas:'
        if not self.iBox.cota_chat.active_cotas:
            header = '*Não tem nenhuma cota!*'
        cota_views = [CotaButtonView(cota) for cota in self.iBox.cota_chat.active_cotas.values()]
        button_list = [cota_view.btn() for cota_view in cota_views]
        new_cota_btn = InlineKeyboardButton('Nova Cota', callback_data='new_cota')
        close_ibox_btn = InlineKeyboardButton('Fechar', callback_data='close_ibox')
        
        menu = [[b] for b in button_list] + [[close_ibox_btn, new_cota_btn]]
        
        bot.edit_message_text(header, 
                              reply_markup=InlineKeyboardMarkup(menu),
                              chat_id=self.iBox.cota_chat._id, 
                              message_id=self.iBox.message_id, 
                              parse_mode=ParseMode.MARKDOWN)

class CotaCreationState:
    def __init__(self, iBox):
        self.iBox = iBox
        self.state = 0

    def update(self, bot):
        cancel_button = InlineKeyboardButton('Cancelar', callback_data='cancel_new_cota')
        if self.state == 0:
            header = 'Qual o nome da cota?'
            button_list = [[cancel_button]]
        elif self.state == 1:
            header = 'Quanto custa a cota?'
            button_list = [[cancel_button, InlineKeyboardButton('Pular >>', callback_data='skip_new_cota_value')]]
        else:
            return

        bot.edit_message_text(header, 
                              reply_markup=InlineKeyboardMarkup(button_list),
                              chat_id=self.iBox.cota_chat._id, 
                              message_id=self.iBox.message_id, 
                              parse_mode=ParseMode.MARKDOWN)

class CotaViewState:
    def __init__(self, iBox, cota):
        self.iBox = iBox
        self.cota = cota

    def update(self, bot):
        n = len(self.cota.going)
        value = self.cota.value

        header = '\[{}] *{}* {}\n'.format(n, self.cota.name, '- R$ {:.02f}'.format(value) if value else '')
        sub_header = '_R$ {:.02f} p/ cada_\n\n'.format(value/n) if (value and n>0) else '\n'
        text = '\n'.join(['{} - {}{}'.format(i+1, user.first_name, ' {}.'.format(user.last_name[0]) if user.last_name else '') for i, user in enumerate(self.cota.going.values())])
        if n == 0:
            text = 'Por enquanto ninguém!'

        not_going_btn = InlineKeyboardButton('Não vou mais', callback_data='remove_participant {}'.format(self.cota._id))
        going_btn = InlineKeyboardButton('Eu vou!', callback_data='new_participant {}'.format(self.cota._id))
        edit_value_btn = InlineKeyboardButton('Edt. Valor', callback_data='edit_value {}'.format(self.cota._id))
        close_cota_btn = InlineKeyboardButton('Fin. Cota', callback_data='close_cota {}'.format(self.cota._id))
        back_btn = InlineKeyboardButton('<< Voltar', callback_data='back_to_main_list')
        
        menu = [[not_going_btn, going_btn],
                [back_btn, edit_value_btn, close_cota_btn]]
        
        bot.edit_message_text(header + sub_header + text + '\n', 
                              reply_markup=InlineKeyboardMarkup(menu),
                              chat_id=self.iBox.cota_chat._id, 
                              message_id=self.iBox.message_id, 
                              parse_mode=ParseMode.MARKDOWN)

class CloseCotaConfirmationState:
    def __init__(self, iBox, cota):
        self.iBox = iBox
        self.cota = cota

    def update(self, bot):
        header = 'Tem certeza que quer finalizar a cota?'

        cancel_btn = InlineKeyboardButton('Cancelar', callback_data='cancel_closing_cota')
        confirm_btn = InlineKeyboardButton('Sim!', callback_data='confirm_closing_cota')
        
        menu = [[cancel_btn, confirm_btn]]
        
        bot.edit_message_text(header,
                              reply_markup=InlineKeyboardMarkup(menu),
                              chat_id=self.iBox.cota_chat._id, 
                              message_id=self.iBox.message_id, 
                              parse_mode=ParseMode.MARKDOWN)


class InteractiveBox:
    def __init__(self, cota_chat, initial_state = None):
        if not initial_state:
            initial_state = MainListState(self)
        self.message_id = None
        self.cota_chat = cota_chat
        
        self.current_state = initial_state

    def reset(self, bot):
        self.load_state(bot, MainListState(self))

    def load_state(self, bot, state):
        self.current_state = state
        self.update(bot)

    def update(self, bot):
        if not self.message_id:
            message = bot.send_message(self.cota_chat._id, "_..._", parse_mode=ParseMode.MARKDOWN)
            self.message_id = message.message_id
        self.current_state.update(bot)

class CotaChat:
    def __init__(self, _id):
        self._id = _id
        self.iBoxes = {}
        
        self.next_cota_id = 0
        self.active_cotas = {}
        self.cota_history = []
        
        self.new_cota_ibox = None
        self.tmp_new_cota = None

        self.iBox_used_to_edit_cota = None
        self.cota_being_edited = None
        
    def new_ibox(self, bot):
        iBox = InteractiveBox(self)
        iBox.update(bot)
        self.iBoxes[iBox.message_id] = iBox
        save_state()

    def remove_ibox(self, bot, message_id):
        bot.delete_message(self._id, message_id)
        del self.iBoxes[message_id]
        save_state()

    def bring_iBox_to_front(self, bot, message_id, reset=False, state=None):
        iBox = self.iBoxes[message_id]
        self.remove_ibox(bot, message_id)
        iBox.message_id = None
        if reset:
            iBox.reset(bot)
        elif state:
            iBox.load_state(bot, state)
        else:
            iBox.update(bot)
        self.iBoxes[iBox.message_id] = iBox
        save_state()

    def update(self, bot):
        for icb in self.iBoxes.values():
            icb.update(bot)
        save_state()

    def close_cota(self, cota_id):
        self.cota_history = [self.active_cotas[cota_id]] + self.cota_history
        del self.active_cotas[cota_id]
        save_state()

    def start_cota_creation(self, bot, message_id, creator_id):
        if self.new_cota_ibox:
            self.remove_ibox(bot, self.new_cota_ibox.message_id)
            self.tmp_new_cota = None

        iBox = self.iBoxes[message_id]
        self.bring_iBox_to_front(bot, message_id, state=CotaCreationState(iBox))
        self.new_cota_ibox = iBox
        self.tmp_new_cota = Cota(self.next_cota_id, creator_id)
        save_state()

    def cota_creation_update(self, bot, message):
        if not self.tmp_new_cota.name:
            self.tmp_new_cota.name = message
            self.new_cota_ibox.current_state.state = 1
            self.bring_iBox_to_front(bot, self.new_cota_ibox.message_id)
        else:
            self.tmp_new_cota.set_value(message)
            self.submit_tmp_new_cota(bot)

    def cancel_tmp_new_cota(self, bot):
        self.tmp_new_cota = None
        self.bring_iBox_to_front(bot, self.new_cota_ibox.message_id, reset=True)
        self.new_cota_ibox = None
        save_state()
            
    def submit_tmp_new_cota(self, bot):
        self.active_cotas[self.tmp_new_cota._id] = self.tmp_new_cota
        logger.info('Cota "%s" created', self.tmp_new_cota.name)
        self.tmp_new_cota = None
        self.next_cota_id += 1
        self.bring_iBox_to_front(bot, self.new_cota_ibox.message_id, reset=True)
        self.new_cota_ibox = None
        save_state()

    def open_cota_view(self, bot, ibox_id, cota_id):
        iBox = self.iBoxes[ibox_id]
        cota = self.active_cotas[cota_id]
        iBox.current_state = CotaViewState(iBox, cota)
        iBox.update(bot)
        save_state()

    def add_cota_participant(self, bot, cota_id, user):
        cota = self.active_cotas[cota_id]
        if user.id not in cota.going:
            cota.add_participant(user.id, user.first_name, user.last_name)
            self.update(bot)
            logger.info('Added participant %s to cota %s', user.id, cota.name)
            save_state()

    def remove_cota_participant(self, bot, cota_id, user):
        cota = self.active_cotas[cota_id]
        if user.id in cota.going:
            cota.remove_participant(user.id)
            self.update(bot)
            logger.info('Removed participant %s to cota %s', user.id, cota.name)
            save_state()

    def try_to_edit_cota_value(self, bot, message_id, cota_id, user_id):
        cota = self.active_cotas[cota_id]
        if cota.creator_id == user_id:
            self.iBox_used_to_edit_cota = self.iBoxes[message_id]
            self.cota_being_edited = cota
            self.bring_iBox_to_front(bot, message_id)
            bot.edit_message_text('Qual o valor da cota?', self._id, self.iBox_used_to_edit_cota.message_id)
        else:
            self.show_not_creator_of_cota_error(bot)

    def edit_cota_value(self, bot, user_id, value):
        if self.cota_being_edited.creator_id == user_id:
            self.cota_being_edited.set_value(value)
            self.bring_iBox_to_front(bot, self.iBox_used_to_edit_cota.message_id,
                state=CotaViewState(self.iBox_used_to_edit_cota, self.cota_being_edited))
            self.iBox_used_to_edit_cota = None
            self.cota_being_edited = None
        else:
            self.show_not_creator_of_cota_error(bot)

    def try_to_close_cota(self, bot, message_id, cota_id, user_id):
        cota = self.active_cotas[cota_id]
        if cota.creator_id == user_id:
            iBox = self.iBoxes[message_id]
            iBox.load_state(bot, CloseCotaConfirmationState(iBox, cota))
        else:
            self.show_not_creator_of_cota_error(bot)

    def cancel_closing_cota(self, bot, message_id, user_id):
        iBox = self.iBoxes[message_id]
        cota = iBox.current_state.cota
        if cota.creator_id == user_id:
            iBox.load_state(bot, CotaViewState(iBox, cota))
        else:
            self.show_not_creator_of_cota_error(bot)

    def confirm_closing_cota(self, bot, message_id, user_id):
        iBox = self.iBoxes[message_id]
        cota = iBox.current_state.cota
        if cota.creator_id == user_id:
            self.close_cota(cota._id)
            iBox.reset(bot)
        else:
            self.show_not_creator_of_cota_error(bot)
        


    def show_not_creator_of_cota_error(self, bot):
        def show_message_on_thread(bot):
            m = bot.send_message(self._id, 'Apenas quem criou a cota pode editar ou finalizá-la',
                parse_mode=ParseMode.MARKDOWN)
            time.sleep(2)
            bot.delete_message(self._id, m.message_id)
        Thread(target=show_message_on_thread, args=(bot,)).start()


def get_cota_chat(update):
    chat_id = update.effective_chat.id
    
    # Add this chat if not present
    if chat_id not in cota_chats:
        cota_chats[chat_id] = CotaChat(chat_id)
        
    return cota_chats[chat_id]

@send_typing_action
def cotas(bot, update):
    cota_chat = get_cota_chat(update)
    cota_chat.new_ibox(bot)

def handle_message(bot, update):
    cota_chat = get_cota_chat(update)
    if cota_chat.new_cota_ibox:
        cota_chat.cota_creation_update(bot, update.message.text)
    if cota_chat.cota_being_edited:
        cota_chat.edit_cota_value(bot, update.effective_user.id, update.message.text)
    
def new_cota(bot, update, message_id, creator_id):
    cota_chat = get_cota_chat(update)
    cota_chat.start_cota_creation(bot, message_id, creator_id)

def cancel_new_cota(bot, update):
    cota_chat = get_cota_chat(update)
    cota_chat.cancel_tmp_new_cota(bot)

def skip_cota_value(bot, update):
    cota_chat = get_cota_chat(update)
    cota_chat.cota_creation_update(bot, None)

def close_ibox(bot, update, m_id):
    cota_chat = get_cota_chat(update)
    cota_chat.remove_ibox(bot, m_id)

def open_cota_view(bot, update, m_id, cota_id):
    cota_chat = get_cota_chat(update)
    cota_chat.open_cota_view(bot, m_id, cota_id)

def back_to_main_list(bot, update, m_id):
    cota_chat = get_cota_chat(update)
    cota_chat.iBoxes[m_id].reset(bot)

def new_participant(bot, update, cota_id, user):
    cota_chat = get_cota_chat(update)
    cota_chat.add_cota_participant(bot, cota_id, user)

def remove_participant(bot, update, cota_id, user):
    cota_chat = get_cota_chat(update)
    cota_chat.remove_cota_participant(bot, cota_id, user)

def edit_cota_value(bot, update, m_id, cota_id, user_id):
    cota_chat = get_cota_chat(update)
    cota_chat.try_to_edit_cota_value(bot, m_id, cota_id, user_id)

def close_cota(bot, update, m_id, cota_id, user_id):
    cota_chat = get_cota_chat(update)
    cota_chat.try_to_close_cota(bot, m_id, cota_id, user_id)

def cancel_closing_cota(bot, update, m_id, user_id):
    cota_chat = get_cota_chat(update)
    cota_chat.cancel_closing_cota(bot, m_id, user_id)

def confirm_closing_cota(bot, update, m_id, user_id):
    cota_chat = get_cota_chat(update)
    cota_chat.confirm_closing_cota(bot, m_id, user_id)
    
def callback_handler(bot, update):
    query = update.callback_query
    splt = query.data.split()
    m_id = query.message.message_id
    user = update.effective_user

    request = splt[0]
    
    if request == 'show_cota':
        open_cota_view(bot, update, m_id, int(splt[1]))
    elif request == 'new_cota':
        new_cota(bot, update, m_id, user.id)
    elif request == 'cancel_new_cota':
        cancel_new_cota(bot, update)
    elif request == 'skip_new_cota_value':
        skip_cota_value(bot, update)
    elif request == 'close_ibox':
        close_ibox(bot, update, m_id)
    elif request == 'back_to_main_list':
        back_to_main_list(bot, update, m_id)
    elif request == 'new_participant':
        new_participant(bot, update, int(splt[1]), user)
    elif request == 'remove_participant':
        remove_participant(bot, update, int(splt[1]), user)
    elif request == 'edit_value':
        edit_cota_value(bot, update, m_id, int(splt[1]), user.id)
    elif request == 'close_cota':
        close_cota(bot, update, m_id, int(splt[1]), user.id)
    elif request == 'cancel_closing_cota':
        cancel_closing_cota(bot, update, m_id, user.id)
    elif request == 'confirm_closing_cota':
        confirm_closing_cota(bot, update, m_id, user.id)

def cota_help(bot, update):
    cota_chat = get_cota_chat(update)
    bot.send_message(cota_chat._id, "Tenta dar um /cotas")

def error(bot, update, error):
    """Log Errors caused by Updates."""
    logger.warning('%s', error)

cota_chats = {}

def load_state():
    try:
        with open('cotas_db.pickle', 'rb') as f:
            global cota_chats
            cota_chats = pickle.load(f)
    except:
        cota_chats = {}

def save_state():
    with open('cotas_db.pickle', 'wb') as f:
        pickle.dump(cota_chats, f)

def main():

    load_state()

    # Create the Updater and pass it your bot's token.
    # Make sure to set use_context=True to use the new context based callbacks
    # Post version 12 this will no longer be necessary
    updater = Updater("692336058:AAGFMBpvydprPwlYgQjwMM1QK66oH41qXfA")

    # Get the dispatcher to register handlers
    dp = updater.dispatcher

    # add handlers
    dp.add_handler(CommandHandler('help', cota_help))

    dp.add_handler(CommandHandler('cotas', cotas))

    dp.add_handler(MessageHandler(Filters.text, handle_message))
    
    dp.add_handler(CallbackQueryHandler(callback_handler))

    # log all errors
    dp.add_error_handler(error)

    # Start the Bot
    updater.start_polling()

    # Run the bot until you press Ctrl-C or the process receives SIGINT,
    # SIGTERM or SIGABRT. This should be used most of the time, since
    # start_polling() is non-blocking and will stop the bot gracefully.
    updater.idle()


if __name__ == '__main__':
    main()

