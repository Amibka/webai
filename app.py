import requests
from flask import Flask, render_template, redirect, url_for, request, flash, jsonify
from flask_login import LoginManager, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from models import db, User, Chat, Message, UserSettings
from dotenv import load_dotenv
import os
from waitress import serve

load_dotenv()

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv("SECRET_KEY")  # Замените в продакшене
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///app.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)

login_manager = LoginManager()
login_manager.login_view = 'login'
login_manager.init_app(app)

LM_STUDIO_IP = os.getenv("LM_STUDIO_IP")
LM_STUDIO_PORT = os.getenv("LM_STUDIO_PORT")

LM_STUDIO_URL = (
    f"http://{LM_STUDIO_IP}:{LM_STUDIO_PORT}/v1/chat/completions"
)


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


# Автоматическое создание таблиц БД при первом запуске
with app.app_context():
    db.create_all()


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')

        if User.query.filter_by(username=username).first():
            flash('Пользователь уже существует.', 'danger')
            return redirect(url_for('register'))

        hashed_pwd = generate_password_hash(password, method='pbkdf2:sha256')
        new_user = User(username=username, password=hashed_pwd)
        db.session.add(new_user)
        db.session.commit()

        login_user(new_user)
        return redirect(url_for('chat_redirect'))
    return render_template('register.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        user = User.query.filter_by(username=username).first()

        if user and check_password_hash(user.password, password):
            login_user(user)
            return redirect(url_for('chat_redirect'))
        flash('Неверное имя пользователя или пароль', 'danger')
    return render_template('login.html')


@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))


@app.route('/chat')
@login_required
def chat_redirect():
    chat = Chat.query.filter_by(user_id=current_user.id).order_by(Chat.id.desc()).first()
    if not chat:
        chat = Chat(user_id=current_user.id)
        db.session.add(chat)
        db.session.commit()
    return redirect(url_for('chat_view', chat_id=chat.id))


@app.route('/chat/new')
@login_required
def new_chat():
    chat = Chat(user_id=current_user.id)
    db.session.add(chat)
    db.session.commit()
    return redirect(url_for('chat_view', chat_id=chat.id))


@app.route('/settings', methods=['GET', 'POST'])
@login_required
def user_settings_route():
    user_settings = UserSettings.query.filter_by(user_id=current_user.id).first()
    if not user_settings:
        user_settings = UserSettings(user_id=current_user.id)
        db.session.add(user_settings)
        db.session.commit()

    if request.method == 'POST':
        data = request.get_json()
        if data:
            user_settings.theme = data.get('theme', 'dark')
            db.session.commit()
            return jsonify({"success": True})
        return jsonify({"success": False, "error": "No data"})

    return jsonify({
        "theme": user_settings.theme
    })


@app.route('/chat/<int:chat_id>/rename', methods=['POST'])
@login_required
def chat_rename(chat_id):
    chat = Chat.query.filter_by(id=chat_id, user_id=current_user.id).first_or_404()
    data = request.get_json()
    new_title = data.get('title', '').strip()
    if new_title:
        chat.title = new_title
        db.session.commit()
        return jsonify({"success": True})
    return jsonify({"success": False, "error": "Пустое название"}), 400


@app.route('/chat/<int:chat_id>/delete', methods=['POST'])
@login_required
def chat_delete(chat_id):
    chat = Chat.query.filter_by(id=chat_id, user_id=current_user.id).first_or_404()
    Message.query.filter_by(chat_id=chat.id).delete()
    db.session.delete(chat)
    db.session.commit()
    return jsonify({"success": True})


@app.route('/chat/<int:chat_id>', methods=['GET', 'POST'])
@login_required
def chat_view(chat_id):
    chat = Chat.query.filter_by(id=chat_id, user_id=current_user.id).first_or_404()

    if request.method == 'POST':
        is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'

        action = request.form.get('action')

        if action == 'regenerate':
            # Удаляем последнее сообщение ассистента, чтобы сгенерировать заново
            last_msg = Message.query.filter_by(chat_id=chat.id).order_by(Message.id.desc()).first()
            if last_msg and last_msg.role == 'assistant':
                db.session.delete(last_msg)
                db.session.commit()
            user_text = ""  # Ничего не добавляем, просто генерируем
            full_message = ""
        elif action == 'edit':
            # Удаляем все сообщения начиная с редактируемого
            msg_id = request.form.get('message_id')
            if msg_id:
                Message.query.filter(Message.chat_id == chat.id, Message.id >= msg_id).delete()
                db.session.commit()
            user_text = request.form.get('message', '')
            # Файлы пока не поддерживаем при редактировании для простоты, или поддерживаем так же
        else:
            user_text = request.form.get('message', '')

        file = request.files.get('file')
        context_text = ""

        # Обработка загруженного файла
        if file and file.filename:
            if file.filename.endswith('.txt') or file.filename.endswith('.csv'):
                file_content = file.read().decode('utf-8', errors='ignore')
                context_text = f"\n\n[FILE_ATTACHMENT:{file.filename}]\n{file_content}"
            else:
                if is_ajax:
                    return jsonify({"error": "Поддерживаются только текстовые файлы (.txt) и .csv"}), 400
                flash("Поддерживаются только текстовые файлы (.txt) и .csv", "warning")
                return redirect(url_for('chat_view', chat_id=chat.id))

        if action != 'regenerate':
            full_message = user_text + context_text

        if action == 'regenerate' or full_message.strip():
            chat_title_updated = False
            new_title = ""
            if action != 'regenerate' and full_message.strip():
                # 1. Сохраняем сообщение пользователя в БД
                msg_user = Message(chat_id=chat.id, role='user', content=full_message)
                db.session.add(msg_user)
                db.session.commit()

                # Обновляем заголовок чата, если это первое сообщение
                if chat.title == "Новый чат":
                    chat.title = user_text[:30] + "..." if len(user_text) > 30 else user_text
                    db.session.commit()
                    chat_title_updated = True
                    new_title = chat.title

            # 2. Формируем историю диалога для отправки в LM Studio
            messages_history = Message.query.filter_by(chat_id=chat.id).order_by(Message.id.asc()).all()
            api_messages = []

            for m in messages_history:
                api_messages.append({"role": m.role, "content": m.content})

            # 3. Запрос к локальной нейросети (LM Studio)
            try:
                payload = {
                    "messages": api_messages,
                    "temperature": 0.7,
                    "max_tokens": -1
                }
                # Отправляем REST-запрос
                response = requests.post(LM_STUDIO_URL, json=payload, timeout=120)
                response.raise_for_status()

                res_data = response.json()
                msg_data = res_data['choices'][0]['message']

                ai_text = msg_data.get('content') or ""
                reasoning = msg_data.get('reasoning_content')

                if not ai_text.strip():
                    if reasoning:
                        ai_text = "*[Ответ пуст. Нейросеть сгенерировала только размышления]*"
                    else:
                        ai_text = "*[Генерация прервана или ответ оказался пустым]*"

                if reasoning:
                    reasoning_clean = reasoning.replace('\n', ' ')
                    snippet = (reasoning_clean[:60] + '...') if len(reasoning_clean) > 60 else reasoning_clean
                    full_ai_text = f"<details><summary>Мысли Nexus: <span style='opacity: 0.7; font-size: 0.9em; font-weight: normal;'>{snippet}</span></summary>\n\n{reasoning}\n</details>\n\n" + ai_text
                else:
                    full_ai_text = ai_text

                # 4. Сохраняем ответ ИИ
                msg_ai = Message(chat_id=chat.id, role='assistant', content=full_ai_text)
                db.session.add(msg_ai)
                db.session.commit()

                if is_ajax:
                    return jsonify({"success": True, "message": ai_text, "reasoning": reasoning,
                                    "chat_title_updated": chat_title_updated, "new_title": new_title})
            except Exception as e:
                error_msg = f"Ошибка при подключении к LM Studio. Убедитесь, что сервер запущен: {e}"
                if is_ajax:
                    return jsonify({"error": error_msg}), 500
                flash(error_msg, "danger")

        if is_ajax:
            return jsonify({"success": True, "message": ""})

        return redirect(url_for('chat_view', chat_id=chat.id))

    # Загрузка истории текущего чата для отображения
    messages = Message.query.filter_by(chat_id=chat.id).order_by(Message.id.asc()).all()
    all_chats = Chat.query.filter_by(user_id=current_user.id).order_by(Chat.id.desc()).all()

    user_settings = UserSettings.query.filter_by(user_id=current_user.id).first()
    theme = user_settings.theme if user_settings else 'dark'

    return render_template('chat.html', messages=messages, all_chats=all_chats, current_chat=chat, theme=theme)


if __name__ == '__main__':
    serve(app, host='0.0.0.0', port=5000)
