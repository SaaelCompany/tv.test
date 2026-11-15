import smtplib
from email.mime.text import MIMEText

from .config import settings


def send_subscription_email(to: str, sub_url: str) -> None:
    if not settings.smtp_host:
        return
    body = (
        f"Спасибо за покупку!\n\n"
        f"Ваша ссылка на подписку:\n{sub_url}\n\n"
        f"Добавьте её в клиент (Happ, v2rayNG, Streisand, V2Box и т.п.).\n"
    )
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = "Ваша VPN-подписка"
    msg["From"] = settings.smtp_from or settings.smtp_user
    msg["To"] = to

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as s:
        s.starttls()
        if settings.smtp_user:
            s.login(settings.smtp_user, settings.smtp_password)
        s.send_message(msg)