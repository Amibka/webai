from flask import Flask, flash, jsonify, redirect, render_template, request, url_for
from flask_login import LoginManager, current_user, login_required, login_user, logout_user
from werkzeug.security import check_password_hash, generate_password_hash

from config import Config
from models import Chat, Message, User, UserSettings, db
from services.lm_studio import LMStudioClient


login_manager = LoginManager()
login_manager.login_view = "login"


def create_app():
    # Создаём приложение и подключаем расширения в одном месте.
    app = Flask(__name__)
    app.config.from_object(Config)

    db.init_app(app)
    login_manager.init_app(app)

    # Автоматически создаём таблицы при первом запуске.
    with app.app_context():
        db.create_all()

    return app


app = create_app()
lm_studio = LMStudioClient(Config.LM_STUDIO_IP, Config.LM_STUDIO_PORT)


@app.context_processor
def inject_template_config():
    return {
        "supported_file_extensions": ",".join(Config.SUPPORTED_FILE_EXTENSIONS),
    }


@login_manager.user_loader
def load_user(user_id):
    # Flask-Login использует эту функцию, чтобы восстановить пользователя из сессии.
    return db.session.get(User, int(user_id))


def is_ajax_request():
    return request.headers.get("X-Requested-With") == "XMLHttpRequest"


def get_or_create_user_settings():
    user_settings = UserSettings.query.filter_by(user_id=current_user.id).first()
    if user_settings:
        return user_settings

    user_settings = UserSettings(user_id=current_user.id)
    db.session.add(user_settings)
    db.session.commit()
    return user_settings


def get_chat_or_404(chat_id):
    return Chat.query.filter_by(id=chat_id, user_id=current_user.id).first_or_404()


def get_latest_or_create_chat():
    chat = Chat.query.filter_by(user_id=current_user.id).order_by(Chat.id.desc()).first()
    if chat:
        return chat

    chat = Chat(user_id=current_user.id)
    db.session.add(chat)
    db.session.commit()
    return chat


def build_api_messages(chat_id):
    # LM Studio принимает историю в формате OpenAI Chat Completions.
    messages = Message.query.filter_by(chat_id=chat_id).order_by(Message.id.asc()).all()
    return [{"role": message.role, "content": message.content} for message in messages]


def read_text_attachment(file):
    # Файлы не сохраняем на диск: читаем текст и добавляем его к сообщению как контекст.
    if not file or not file.filename:
        return ""

    if not file.filename.endswith(Config.SUPPORTED_FILE_EXTENSIONS):
        raise ValueError("Поддерживаются только текстовые файлы (.txt) и .csv")

    file_content = file.read().decode("utf-8", errors="ignore")
    return f"\n\n[FILE_ATTACHMENT:{file.filename}]\n{file_content}"


def normalize_ai_response(content, reasoning):
    # Некоторые reasoning-модели могут вернуть мысли без обычного content.
    if content.strip():
        return content

    if reasoning:
        return "*[Ответ пуст. Нейросеть сгенерировала только размышления]*"

    return "*[Генерация прервана или ответ оказался пустым]*"


def build_stored_ai_message(content, reasoning):
    # В базе храним и финальный ответ, и reasoning-блок, чтобы история открывалась полностью.
    if not reasoning:
        return content

    reasoning_clean = reasoning.replace("\n", " ")
    snippet = f"{reasoning_clean[:60]}..." if len(reasoning_clean) > 60 else reasoning_clean
    return (
        "<details>"
        f"<summary>Мысли Nexus: <span class='reasoning-snippet'>{snippet}</span></summary>\n\n"
        f"{reasoning}\n"
        "</details>\n\n"
        f"{content}"
    )


def maybe_update_chat_title(chat, user_text):
    # Первый пользовательский запрос становится названием нового чата.
    if chat.title != Config.DEFAULT_CHAT_TITLE or not user_text.strip():
        return False, ""

    chat.title = f"{user_text[:30]}..." if len(user_text) > 30 else user_text
    db.session.commit()
    return True, chat.title


def delete_messages_from(chat_id, message_id):
    # При редактировании сообщения удаляем старую ветку диалога, начиная с изменённого сообщения.
    try:
        start_id = int(message_id)
    except (TypeError, ValueError):
        return

    Message.query.filter(Message.chat_id == chat_id, Message.id >= start_id).delete()
    db.session.commit()


def regenerate_last_response(chat):
    # Перегенерация удаляет только последний ответ ассистента и оставляет запрос пользователя.
    last_message = Message.query.filter_by(chat_id=chat.id).order_by(Message.id.desc()).first()
    if last_message and last_message.role == "assistant":
        db.session.delete(last_message)
        db.session.commit()


def save_user_message(chat, content):
    message = Message(chat_id=chat.id, role="user", content=content)
    db.session.add(message)
    db.session.commit()


def save_ai_message(chat, content):
    message = Message(chat_id=chat.id, role="assistant", content=content)
    db.session.add(message)
    db.session.commit()


def generate_and_store_ai_response(chat):
    # Собираем историю, отправляем её в LM Studio и сохраняем ответ модели.
    api_messages = build_api_messages(chat.id)
    result = lm_studio.complete_chat(api_messages)

    ai_text = normalize_ai_response(result["content"], result["reasoning"])
    stored_text = build_stored_ai_message(ai_text, result["reasoning"])
    save_ai_message(chat, stored_text)

    return ai_text, result["reasoning"]


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")

        if User.query.filter_by(username=username).first():
            flash("Пользователь уже существует.", "danger")
            return redirect(url_for("register"))

        user = User(
            username=username,
            password=generate_password_hash(password, method="pbkdf2:sha256"),
        )
        db.session.add(user)
        db.session.commit()

        login_user(user)
        return redirect(url_for("chat_redirect"))

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        user = User.query.filter_by(username=username).first()

        if user and check_password_hash(user.password, password):
            login_user(user)
            return redirect(url_for("chat_redirect"))

        flash("Неверное имя пользователя или пароль", "danger")

    return render_template("login.html")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))


@app.route("/chat")
@login_required
def chat_redirect():
    chat = get_latest_or_create_chat()
    return redirect(url_for("chat_view", chat_id=chat.id))


@app.route("/chat/new")
@login_required
def new_chat():
    chat = Chat(user_id=current_user.id)
    db.session.add(chat)
    db.session.commit()
    return redirect(url_for("chat_view", chat_id=chat.id))


@app.route("/settings", methods=["GET", "POST"])
@login_required
def user_settings_route():
    user_settings = get_or_create_user_settings()

    if request.method == "POST":
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "No data"})

        user_settings.theme = data.get("theme", "dark")
        db.session.commit()
        return jsonify({"success": True})

    return jsonify({"theme": user_settings.theme})


@app.route("/chat/<int:chat_id>/rename", methods=["POST"])
@login_required
def chat_rename(chat_id):
    chat = get_chat_or_404(chat_id)
    data = request.get_json() or {}
    new_title = data.get("title", "").strip()

    if not new_title:
        return jsonify({"success": False, "error": "Пустое название"}), 400

    chat.title = new_title
    db.session.commit()
    return jsonify({"success": True})


@app.route("/chat/<int:chat_id>/delete", methods=["POST"])
@login_required
def chat_delete(chat_id):
    chat = get_chat_or_404(chat_id)
    db.session.delete(chat)
    db.session.commit()
    return jsonify({"success": True})


@app.route("/chat/<int:chat_id>", methods=["GET", "POST"])
@login_required
def chat_view(chat_id):
    chat = get_chat_or_404(chat_id)

    if request.method == "POST":
        # Один endpoint обслуживает обычную отправку, редактирование и перегенерацию.
        ajax = is_ajax_request()
        action = request.form.get("action")
        user_text = request.form.get("message", "")
        full_message = ""
        chat_title_updated = False
        new_title = ""

        if action == "regenerate":
            regenerate_last_response(chat)
        else:
            if action == "edit":
                delete_messages_from(chat.id, request.form.get("message_id"))

            try:
                # Текст вложенного файла добавляется в prompt, но сам файл не хранится.
                context_text = read_text_attachment(request.files.get("file"))
            except ValueError as error:
                if ajax:
                    return jsonify({"error": str(error)}), 400

                flash(str(error), "warning")
                return redirect(url_for("chat_view", chat_id=chat.id))

            full_message = user_text + context_text
            if full_message.strip():
                save_user_message(chat, full_message)
                chat_title_updated, new_title = maybe_update_chat_title(chat, user_text)

        if action == "regenerate" or full_message.strip():
            try:
                # Долгий вызов локальной модели: ошибки возвращаем в UI понятным сообщением.
                ai_text, reasoning = generate_and_store_ai_response(chat)
            except Exception as error:
                error_msg = (
                    "Ошибка при подключении к LM Studio. "
                    f"Убедитесь, что сервер запущен: {error}"
                )
                if ajax:
                    return jsonify({"error": error_msg}), 500

                flash(error_msg, "danger")
            else:
                if ajax:
                    return jsonify(
                        {
                            "success": True,
                            "message": ai_text,
                            "reasoning": reasoning,
                            "chat_title_updated": chat_title_updated,
                            "new_title": new_title,
                        }
                    )

        if ajax:
            return jsonify({"success": True, "message": ""})

        return redirect(url_for("chat_view", chat_id=chat.id))

    messages = Message.query.filter_by(chat_id=chat.id).order_by(Message.id.asc()).all()
    all_chats = Chat.query.filter_by(user_id=current_user.id).order_by(Chat.id.desc()).all()
    user_settings = UserSettings.query.filter_by(user_id=current_user.id).first()
    theme = user_settings.theme if user_settings else "dark"

    return render_template(
        "chat.html",
        messages=messages,
        all_chats=all_chats,
        current_chat=chat,
        theme=theme,
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", debug=False, port=5000)
