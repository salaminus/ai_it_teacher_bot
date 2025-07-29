import sqlite3
import telebot
import os
from telebot import types
from config import TKN
from datetime import datetime

bot = telebot.TeleBot(TKN)


# Подключение к базе данных SQLite
db_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'bot', 'tests.db'))
conn = sqlite3.connect(db_path, check_same_thread=False)
cursor = conn.cursor()


# Переменные для отслеживания состояния пользователя
user_state = {}
user_answers = {}
current_question = {}

# Создаем постоянное меню
def set_main_menu():
    main_menu = types.ReplyKeyboardMarkup(resize_keyboard=True)
    main_menu.row(types.KeyboardButton('📝 Тесты'), types.KeyboardButton('📊 Результаты'))
    main_menu.row(types.KeyboardButton('ℹ️ Помощь'), types.KeyboardButton('⚙️ Настройки'))
    return main_menu

# Обработчик команды /start
@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.send_message(
        message.chat.id,
        "Привет! Я бот для тестирования. Выбери опцию:",
        reply_markup=set_main_menu()
    )
    # Добавляем пользователя в базу, если его там нет
    cursor.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (message.chat.id,))
    conn.commit()

# Обработчик команды /help
@bot.message_handler(commands=['help'])
def send_help(message):
    help_text = """
ℹ️ <b>Справочная информация </b>

📝 <b>Как пройти тест:</b>
1. Нажмите кнопку "Тесты"
2. Выберите нужный тест
3. Отвечайте на вопросы по порядку
4. Получите результат после завершения теста

📊 <b>Просмотр результатов:</b>
Нажмите кнопку "Результаты" для просмотра ваших предыдущих попыток
"""
    bot.send_message(message.chat.id, help_text, parse_mode='HTML')

# Обработчик текстовых сообщений
@bot.message_handler(func=lambda message: True)
def handle_text(message):
    if message.text == '📝 Тесты':
        show_tests(message.chat.id)
    elif message.text == '📊 Результаты':
        show_results_menu(message.chat.id)
    elif message.text == 'ℹ️ Помощь':
        send_help(message)
    elif message.text == '⚙️ Настройки':
        show_settings(message.chat.id)
    else:
        # Проверяем, находится ли пользователь в процессе ответа на вопрос
        if message.chat.id in user_state and user_state[message.chat.id] == 'answering':
            process_answer(message)

# Показать настройки
def show_settings(chat_id):
    settings_markup = types.InlineKeyboardMarkup()
    settings_markup.add(
        types.InlineKeyboardButton("Уведомления", callback_data="settings_notifications")
    )
    bot.send_message(chat_id, "⚙️ <b>Настройки бота:</b>", reply_markup=settings_markup, parse_mode='HTML')

# Показать список тестов
def show_tests(chat_id):
    cursor.execute("SELECT test_id, test_name FROM tests")
    tests = cursor.fetchall()
    
    if not tests:
        bot.send_message(chat_id, "На данный момент нет доступных тестов.")
        return
    
    markup = types.InlineKeyboardMarkup()
    for test in tests:
        markup.add(types.InlineKeyboardButton(text=test[1], callback_data=f"test_{test[0]}"))
    
    bot.send_message(chat_id, "📝 <b>Выберите тест:</b>", reply_markup=markup, parse_mode='HTML')

# Показать меню результатов
def show_results_menu(chat_id):
    cursor.execute('''
    SELECT r.result_id, t.test_name, r.score, r.total_questions, r.timestamp 
    FROM results r
    JOIN tests t ON r.test_id = t.test_id
    WHERE r.user_id = ?
    ORDER BY r.timestamp DESC
    ''', (chat_id,))
    
    results = cursor.fetchall()
    
    if not results:
        bot.send_message(chat_id, "У вас пока нет результатов тестов.")
        return
    
    markup = types.InlineKeyboardMarkup()
    for result in results:
        date_str = datetime.strptime(result[4], '%Y-%m-%d %H:%M:%S').strftime('%d.%m.%Y %H:%M')
        markup.add(types.InlineKeyboardButton(
            text=f"{result[1][:20]} - {result[2]}/{result[3]} ({date_str})",
            callback_data=f"result_{result[0]}"
        ))
    
    bot.send_message(chat_id, "📊 <b>Ваши результаты:</b>", reply_markup=markup, parse_mode='HTML')

# Обработчик inline-кнопок
@bot.callback_query_handler(func=lambda call: True)
def callback_query(call):
    if call.data.startswith("test_"):
        test_id = int(call.data.split("_")[1])
        start_test(call.message.chat.id, test_id)
    elif call.data.startswith("result_"):
        result_id = int(call.data.split("_")[1])
        show_result_details(call.message.chat.id, result_id)
    elif call.data.startswith("settings_"):
        handle_settings(call)
    elif call.data == "back_to_results":
        show_results_menu(call.message.chat.id)

# Обработка настроек
def handle_settings(call):
    setting = call.data.split("_")[1]
    if setting == "notifications":
        bot.answer_callback_query(call.id, "Настройки уведомлений будут доступны в следующей версии")


# Начать тест
def start_test(chat_id, test_id):
    # Получаем вопросы для теста
    cursor.execute("SELECT question_id, question_text FROM questions WHERE test_id = ? ORDER BY question_id", (test_id,))
    questions = cursor.fetchall()
    
    if not questions:
        bot.send_message(chat_id, "В этом тесте пока нет вопросов.")
        return
    
    # Сохраняем состояние пользователя
    user_state[chat_id] = 'answering'
    user_answers[chat_id] = {
        'test_id': test_id,
        'questions': questions,
        'current_question': 0,
        'answers': [],
        'score': 0
    }
    
    # Задаем первый вопрос
    ask_question(chat_id)

# Задать вопрос
def ask_question(chat_id):
    user_data = user_answers[chat_id]
    question_id, question_text = user_data['questions'][user_data['current_question']]
    
    # Сохраняем текущий вопрос для проверки ответа
    current_question[chat_id] = question_id
    
    bot.send_message(
        chat_id, 
        f"❓ <b>Вопрос {user_data['current_question'] + 1}:</b>\n\n{question_text}",
        parse_mode='HTML',
        reply_markup=types.ForceReply(selective=False)
    )

# Обработать ответ
def process_answer(message):
    chat_id = message.chat.id
    user_answer = message.text.strip()
    question_id = current_question[chat_id]
    
    # Получаем правильный ответ
    cursor.execute("SELECT correct_answer FROM questions WHERE question_id = ?", (question_id,))
    correct_answer = cursor.fetchone()[0]
    
    # Проверяем ответ
    is_correct = user_answer.lower() == correct_answer.lower()
    if is_correct:
        user_answers[chat_id]['score'] += 1
    
    # Сохраняем ответ пользователя
    user_answers[chat_id]['answers'].append({
        'question_id': question_id,
        'user_answer': user_answer,
        'is_correct': is_correct
    })
    
    # Переход к следующему вопросу или завершение теста
    user_answers[chat_id]['current_question'] += 1
    
    if user_answers[chat_id]['current_question'] < len(user_answers[chat_id]['questions']):
        ask_question(chat_id)
    else:
        finish_test(chat_id)

# Завершить тест и сохранить результаты
def finish_test(chat_id):
    user_data = user_answers[chat_id]
    test_id = user_data['test_id']
    score = user_data['score']
    total_questions = len(user_data['questions'])
    
    # Сохраняем результат теста
    cursor.execute(
        "INSERT INTO results (user_id, test_id, score, total_questions) VALUES (?, ?, ?, ?)",
        (chat_id, test_id, score, total_questions)
    )
    result_id = cursor.lastrowid
    
    # Сохраняем ответы пользователя
    for answer in user_data['answers']:
        cursor.execute(
            "INSERT INTO user_answers (result_id, question_id, user_answer, is_correct) VALUES (?, ?, ?, ?)",
            (result_id, answer['question_id'], answer['user_answer'], int(answer['is_correct']))
        )
    
    conn.commit()
    
    # Очищаем состояние пользователя
    del user_state[chat_id]
    del user_answers[chat_id]
    del current_question[chat_id]
    
    # Отправляем результаты
    bot.send_message(
        chat_id,
        f"🎉 <b>Тест завершен!</b>\n\nВаш результат: <b>{score}/{total_questions}</b>",
        parse_mode='HTML',
        reply_markup=set_main_menu()
    )

# Показать детали результата
def show_result_details(chat_id, result_id):
    cursor.execute('''
    SELECT t.test_name, r.score, r.total_questions, r.timestamp
    FROM results r
    JOIN tests t ON r.test_id = t.test_id
    WHERE r.result_id = ?
    ''', (result_id,))
    
    test_info = cursor.fetchone()
    
    cursor.execute('''
    SELECT q.question_text, q.correct_answer, ua.user_answer, ua.is_correct
    FROM user_answers ua
    JOIN questions q ON ua.question_id = q.question_id
    WHERE ua.result_id = ?
    ORDER BY q.question_id
    ''', (result_id,))
    
    questions = cursor.fetchall()
    
    date_str = datetime.strptime(test_info[3], '%Y-%m-%d %H:%M:%S').strftime('%d.%m.%Y %H:%M')
    
    message = f"📊 <b>Результат теста:</b> {test_info[0]}\n"
    message += f"📅 <b>Дата:</b> {date_str}\n"
    message += f"✅ <b>Правильных ответов:</b> {test_info[1]}/{test_info[2]}\n"
    message += f"📈 <b>Успешность:</b> {round(test_info[1]/test_info[2]*100)}%\n\n"
    message += "<b>Детализация:</b>\n\n"
    
    for i, question in enumerate(questions, 1):
        status = "✅" if question[3] else "❌"
        message += f"{i}. {question[0]}\n"
        message += f"   Ваш ответ: {question[2]} {status}\n"
        message += f"   Правильный ответ: {question[1]}\n\n"
    
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("← Назад к результатам", callback_data="back_to_results"))
    
    bot.send_message(chat_id, message, parse_mode='HTML', reply_markup=markup)

# Запуск бота
if __name__ == '__main__':
    print("Бот запущен...")
    bot.infinity_polling()