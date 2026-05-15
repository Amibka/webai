import os
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def env_bool(name):
    return os.getenv(name, "").lower() in ("1", "true", "yes", "on")


def env_list(name):
    value = os.getenv(name, "")
    return tuple(item.strip() for item in value.split(",") if item.strip())


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY")
    SQLALCHEMY_DATABASE_URI = os.getenv("SQLALCHEMY_DATABASE_URI")
    SQLALCHEMY_TRACK_MODIFICATIONS = env_bool("SQLALCHEMY_TRACK_MODIFICATIONS")

    LM_STUDIO_IP = os.getenv("LM_STUDIO_IP")
    LM_STUDIO_PORT = os.getenv("LM_STUDIO_PORT")

    DEFAULT_CHAT_TITLE = os.getenv("DEFAULT_CHAT_TITLE")
    SUPPORTED_FILE_EXTENSIONS = env_list("SUPPORTED_FILE_EXTENSIONS")
