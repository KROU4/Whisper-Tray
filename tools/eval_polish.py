"""Compare AI formatting models on a dictation corpus with the user's Groq key.

Usage: python tools/eval_polish.py [extra_corpus.txt] [--models a,b]
An extra corpus holds one raw transcript per paragraph (blank-line separated).
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from text_cleanup import CLEANUP_MODELS, TextCleaner, _plausible  # noqa: E402
from transcriber import Transcriber  # noqa: E402

CORPUS = [
    "э ну смотри я хочу чтобы ты завтра э созвонился с клиентом и обсудил с ним сроки по проекту потому что они "
    "как бы уже горят и короче если он скажет что не успевает то надо будет э переносить запуск на следующую неделю",
    "напомни мне купить молоко хлеб э яйца и ещё э кофе в зёрнах",
    "встреча в среду в десять утра нет вернее в одиннадцать в переговорке на третьем этаже",
    "по итогам квартала выручка выросла на двадцать три процента а расходы снизились на семь процентов это лучше "
    "чем мы планировали новый абзац теперь про найм нам нужно закрыть три вакансии до конца ноября",
    "что нужно сделать до релиза во первых прогнать тесты во вторых обновить ченджлог в третьих собрать установщики "
    "и в четвёртых задеплоить сайт",
    "слушай а ты можешь мне написать скрипт на питоне который будет парсить json и вытаскивать оттуда все email адреса",
    "так э я посмотрел пул реквест и там в общем есть пара проблем запятая во-первых тесты не проходят на маке "
    "во-вторых линтер ругается на импорты а в целом всё ок можно мёржить после фиксов",
    "um so basically I was thinking we could uh push the release to friday no actually monday and then uh update "
    "the docs and the landing page you know",
    "привет как дела давно не виделись может встретимся на выходных",
    "какая сегодня погода в москве",
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("extra", nargs="?")
    parser.add_argument("--models", default=",".join(CLEANUP_MODELS))
    parser.add_argument("--wait", type=float, default=0.0, help="seconds to wait first (rate-limit reset)")
    parser.add_argument("--only-extra", action="store_true", help="skip the built-in corpus")
    parser.add_argument("--gap", type=float, default=8.0, help="seconds between requests, like real dictation")
    args = parser.parse_args()
    time.sleep(args.wait)
    corpus = [] if args.only_extra else list(CORPUS)
    if args.extra:
        text = Path(args.extra).read_text(encoding="utf-8")
        corpus += [part.strip() for part in text.split("\n\n") if part.strip()]
    transcriber = Transcriber(config={"profile": "speed"})
    client = transcriber._get_groq_client()
    usage = []
    create = client.chat.completions.create

    def counted(**kwargs):
        response = create(**kwargs)
        usage.append(getattr(response.usage, "total_tokens", 0))
        return response

    client.chat.completions.create = counted
    sys.stdout.reconfigure(encoding="utf-8")
    for model in args.models.split(","):
        print(f"\n######## {model}")
        cleaner = TextCleaner(lambda: client, models=(model,))
        total = 0.0
        for raw in corpus:
            time.sleep(args.gap)
            start = time.monotonic()
            out = cleaner.polish(raw, language="ru")
            elapsed = time.monotonic() - start
            total += elapsed
            verdict = "SAME" if out == raw else ("ok" if _plausible(raw, out) else "REJECTED")
            print(f"\n[{elapsed:.2f}s {verdict}] IN: {raw}\nOUT:\n{out}")
        print(f"\n== {model}: total {total:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
