"""(14) Telegram bildirimleri ve uzaktan komutlar.

Komutlar sadece .env'deki TELEGRAM_CHAT_ID'den gelirse kabul edilir:
    /durum       açık pozisyonlar ve kâr/zarar
    /durdur      yeni alımları durdurur (pozisyonlar takip edilmeye devam eder)
    /devam       /durdur'u kaldırır
    /hepsinisat  bütün pozisyonları satar ve alımları durdurur
"""
import logging

import requests

log = logging.getLogger("ajan")


class Notifier:
    def __init__(self, token="", chat_id="", timeout=10):
        self.token = token
        self.chat_id = str(chat_id)
        self.timeout = timeout
        self.offset = None
        self.primed = False  # ilk okumada eski mesajlar atlanır

    @property
    def enabled(self):
        return bool(self.token and self.chat_id)

    def _api(self, method, **params):
        r = requests.post(f"https://api.telegram.org/bot{self.token}/{method}",
                          data=params, timeout=self.timeout)
        r.raise_for_status()
        return r.json().get("result")

    def send(self, text):
        if not self.enabled:
            return
        try:
            self._api("sendMessage", chat_id=self.chat_id, text=text[:4000])
        except Exception as exc:  # bildirim hatası ajanı durdurmamalı
            log.warning("Telegram mesajı gönderilemedi: %s", exc)

    def commands(self):
        """Yeni komutları döner, örn. ["/durum"]."""
        if not self.enabled:
            return []
        try:
            params = {"timeout": 0}
            if self.offset is not None:
                params["offset"] = self.offset
            updates = self._api("getUpdates", **params) or []
        except Exception as exc:
            log.warning("Telegram komutları okunamadı: %s", exc)
            return []
        out = []
        for u in updates:
            self.offset = u["update_id"] + 1
            if not self.primed:
                continue
            msg = u.get("message") or {}
            if str(msg.get("chat", {}).get("id")) != self.chat_id:
                continue  # başkasından gelen komutları yok say
            text = (msg.get("text") or "").strip().split("@")[0].lower()
            if text.startswith("/"):
                out.append(text)
        self.primed = True
        return out
