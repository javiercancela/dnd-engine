"""Dice expressions: NdM[+-K], kh/kl, and d%."""

import hashlib
import random
import re
from collections import namedtuple

# count is optional (d20, d%). keep is kh or kl plus a count. modifier is last.
_EXPR = re.compile(
    r'^(?P<count>\d*)d(?P<sides>\d+|%)'
    r'(?:(?P<keep>kh|kl)(?P<keep_n>\d+))?'
    r'(?P<mod>[+-]\d+)?$'
)

_MAX_DICE = 100

Parsed = namedtuple('Parsed', 'count sides mod keep keep_n')


def parse_expr(expr):
    """Parse one dice expression. Raise ValueError when it is not valid."""
    match = _EXPR.fullmatch(expr or '')
    if match is None:
        raise ValueError(f'bad dice expression {expr!r}')
    raw_count = match.group('count')
    count = int(raw_count) if raw_count else 1
    sides_token = match.group('sides')
    sides = 100 if sides_token == '%' else int(sides_token)
    mod = int(match.group('mod') or 0)
    keep = match.group('keep')
    keep_n = int(match.group('keep_n')) if match.group('keep_n') else None
    if count < 1 or count > _MAX_DICE:
        raise ValueError(f'dice count must be 1..{_MAX_DICE}')
    if sides < 1:
        raise ValueError('dice need at least 1 side')
    if keep and (keep_n < 1 or keep_n > count):
        raise ValueError('keep count must be between 1 and the number of dice')
    return Parsed(count, sides, mod, keep, keep_n)


def roll_expr(expr, seed, counter, advantage):
    """Roll ``expr`` with ``random.Random(sha256(f'{seed}:{counter}'))``.

    ``advantage`` is ``adv``, ``dis``, or ``none``. Advantage and
    disadvantage roll a second face of a single die and keep one.
    """
    parsed = parse_expr(expr)
    if advantage in ('adv', 'dis') and (parsed.count != 1 or parsed.keep):
        raise ValueError('advantage and disadvantage apply to a single die')
    digest = hashlib.sha256(f'{seed}:{counter}'.encode()).digest()
    rng = random.Random(digest)
    times = 2 if advantage in ('adv', 'dis') else parsed.count
    dice = [rng.randint(1, parsed.sides) for _ in range(times)]
    if advantage == 'adv':
        kept = [max(dice)]
    elif advantage == 'dis':
        kept = [min(dice)]
    elif parsed.keep == 'kh':
        kept = _keep(dice, parsed.keep_n, high=True)
    elif parsed.keep == 'kl':
        kept = _keep(dice, parsed.keep_n, high=False)
    else:
        kept = list(dice)
    return {
        'dice': dice,
        'kept': kept,
        'mod': parsed.mod,
        'total': sum(kept) + parsed.mod,
    }


def _keep(dice, keep_n, high):
    """Keep ``keep_n`` faces, preserving the order they were rolled."""
    ranked = sorted(range(len(dice)), key=lambda index: dice[index], reverse=high)
    chosen = sorted(ranked[:keep_n])
    return [dice[index] for index in chosen]
