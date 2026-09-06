"""Compare recorded synthetic QA speech using baseline and current decoders."""

import argparse
import importlib.util
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from transcriber import Transcriber  # noqa: E402


def word_errors(reference, actual):
    left = re.findall(r'\w+', reference.casefold())
    right = re.findall(r'\w+', actual.casefold())
    previous = list(range(len(right) + 1))
    for row, word in enumerate(left, 1):
        current = [row]
        for col, other in enumerate(right, 1):
            current.append(min(current[-1] + 1, previous[col] + 1, previous[col - 1] + (word != other)))
        previous = current
    return previous[-1], len(left)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('cases', type=Path)
    parser.add_argument('--baseline', type=Path)
    parser.add_argument('--cloud', action='store_true')
    parser.add_argument('--model', default='tiny')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    cases = json.loads(args.cases.read_text(encoding='utf-8-sig'))
    current = Transcriber(args.model, {'profile': 'privacy'})
    current.prepare()
    engines = [('current_local', current)]
    if args.baseline:
        spec = importlib.util.spec_from_file_location('baseline_transcriber', args.baseline)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        baseline = module.Transcriber(args.model, {'profile': 'privacy'})
        baseline._model = current._model
        engines.insert(0, ('baseline_local', baseline))
    if args.cloud:
        engines.append(('groq', Transcriber(config={'profile': 'speed'})))
    rows = []
    for name, engine in engines:
        for case in cases:
            start = time.monotonic()
            try:
                text = engine.transcribe(Path(case['path']), language=case['language'])
                errors, words = word_errors(case['text'], text)
                row = dict(engine=name, language=case['language'], text=text, errors=errors,
                           words=words, seconds=round(time.monotonic() - start, 3))
            except Exception as exc:
                row = dict(engine=name, language=case['language'], error=getattr(exc, 'code', type(exc).__name__))
            rows.append(row)
            args.output.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding='utf-8')
            print(json.dumps(row, ensure_ascii=False), flush=True)
    if any('error' in row for row in rows):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
