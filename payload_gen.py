#!/usr/bin/env python3
"""
payload_gen.py — run ANY command in a loop, mutating its input each iteration,
and save the raw command output.

Purpose: wrap a tool that does NOT support files or multiple inputs, and drive
it with a range, a wordlist file, or an inline list — so one invocation of this
script fires the command once per value and collects every output.

The placeholder (default '*') inside each -pay template is replaced by the
current value. The built payloads are exposed to the command as
$PAY / $PAY1 / $PAY2 ... ($PAY is an alias for $PAY1).

A -par (or -w) source can be:
  * a numeric range   start,end[,step]   (end inclusive)   e.g. -par 10000,20000
  * a file path       one value per line                    e.g. -par ids.txt
  * an inline list    comma-separated values                e.g. -par a,b,c,d

Multiple sources: give ONE -par shared by every -pay, OR give one -par per -pay
(same count) and they are ZIPPED together (row i = source1[i], source2[i], ...).

Everything after -cmd is the command to run (run directly, NOT through a shell,
so JSON quoting is safe). Only the command's stdout is written to the file —
nothing extra is added.

Examples:
  # single payload, numeric range
  ./payload_gen.py -pay '{"employeeId":"*"}' -par 10000,20000 -o payload.txt \\
      -cmd ./aescrypt.py -enc '$PAY'

  # values from a file, run 8 in parallel
  ./payload_gen.py -pay '{"user":"*"}' -par users.txt -j 8 -o out.txt \\
      -cmd ./aescrypt.py -enc '$PAY'

  # inline list of strings
  ./payload_gen.py -pay '{"role":"*"}' -par admin,root,manager -o out.txt \\
      -cmd ./aescrypt.py -enc '$PAY'

  # two payloads, each from its own file, zipped
  ./payload_gen.py -pay '{"user":"*"}' -pay '{"pass":"*"}' \\
      -par users.txt -par passwords.txt -o out.txt \\
      -cmd ./aescrypt.py -enc '$PAY1'
"""
import argparse
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

PAY_RE = re.compile(r'\$\{(PAY\d*)\}|\$(PAY\d*)')


def build_parser():
    p = argparse.ArgumentParser(
        prog='payload_gen.py',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Loop a payload template and save each command's output.",
        epilog="""a -par / -w source can be:
  range   start,end[,step]   (end inclusive)   e.g. -par 10000,20000
  file    one value per line                   e.g. -par ids.txt
  list    comma-separated values               e.g. -par admin,root,user

examples:
  ./payload_gen.py -pay '{"employeeId":"*"}' -par 10000,20000 -o payload.txt \\
      -cmd ./aescrypt.py -enc '$PAY'

  ./payload_gen.py -pay '{"user":"*"}' -par users.txt -j 8 -o out.txt \\
      -cmd ./aescrypt.py -enc '$PAY'

  ./payload_gen.py -pay '{"role":"*"}' -par admin,root,manager -o out.txt \\
      -cmd ./aescrypt.py -enc '$PAY'

  # two payloads, each from its own source, zipped row-by-row
  ./payload_gen.py -pay '{"user":"*"}' -pay '{"pass":"*"}' \\
      -par users.txt -par passwords.txt -o out.txt \\
      -cmd ./aescrypt.py -enc '$PAY1'

notes:
  * everything after -cmd is the command (run directly, NOT via a shell).
  * quote '$PAY' so your shell does not expand it; this tool substitutes it.
  * -pay is repeatable -> $PAY1,$PAY2,...  ; $PAY is an alias for $PAY1.
  * one -par is shared by all payloads; N -par (= N -pay) are zipped.
  * only the command's raw stdout is written to -o; nothing extra is added.""")
    p.add_argument('-pay', dest='pay', action='append', required=True, metavar='TEMPLATE',
                   help="payload template with placeholder (repeatable -> $PAY1,$PAY2,...)")
    p.add_argument('-par', dest='par', action='append', metavar='RANGE|FILE|LIST',
                   help="value source: range 'a,b[,s]', a file path, or 'x,y,z' list (repeatable)")
    p.add_argument('-w', '--wordlist', dest='wordlist', action='append', metavar='FILE',
                   help="value source forced to be a FILE (one per line, repeatable)")
    p.add_argument('-o', '--out', required=True, metavar='FILE',
                   help="output file (raw stdout only)")
    p.add_argument('-j', '--jobs', type=int, default=1, metavar='N',
                   help="parallel workers (default 1)")
    p.add_argument('--ph', default='*', metavar='CHAR',
                   help="placeholder char/string (default '*')")
    p.add_argument('--sep', default='', metavar='STR',
                   help="separator written between outputs (default none)")
    p.add_argument('--stop-on-error', action='store_true',
                   help="abort if a command exits non-zero")
    p.add_argument('-cmd', dest='cmd', nargs=argparse.REMAINDER, required=True, metavar='COMMAND ...',
                   help="command to run each iteration; must come LAST")
    return p


def parse_args(argv):
    p = build_parser()
    # Let -h/--help work even without -cmd present.
    if '-h' in argv or '--help' in argv:
        p.parse_args(argv)  # prints help and exits

    # Split off the command manually: everything after the first '-cmd'.
    if '-cmd' not in argv:
        p.error("missing -cmd <command ...> (must come last)")
    idx = argv.index('-cmd')
    head, cmd = argv[:idx], argv[idx + 1:]
    if not cmd:
        p.error("-cmd given but no command follows it")

    args = p.parse_args(head + ['-cmd', 'placeholder'])
    args.cmd = cmd
    return args


def _read_file(path):
    with open(path, encoding='utf-8', errors='replace') as fh:
        return [ln.rstrip('\n') for ln in fh]


def _as_range(spec):
    """Return a list of str values if spec looks like start,end[,step], else None."""
    parts = spec.split(',')
    if len(parts) not in (2, 3):
        return None
    try:
        nums = [int(x) for x in parts]
    except ValueError:
        return None
    start, end = nums[0], nums[1]
    step = nums[2] if len(nums) == 3 else 1
    if step == 0:
        sys.exit("error: step cannot be 0")
    step = abs(step) * (1 if end >= start else -1)
    return [str(v) for v in range(start, end + (1 if step > 0 else -1), step)]


def load_source(spec, force_file=False):
    """A source is a file (if it exists / forced), else a numeric range, else an
    inline comma-separated list."""
    if force_file or os.path.isfile(spec):
        if not os.path.isfile(spec):
            sys.exit(f"error: -w file not found: {spec}")
        return _read_file(spec)
    rng = _as_range(spec)
    if rng is not None:
        return rng
    return spec.split(',')  # literal list of values


def build_rows(args):
    """Return a list of rows; each row has one value per -pay template."""
    sources = [load_source(s) for s in (args.par or [])]
    sources += [load_source(w, force_file=True) for w in (args.wordlist or [])]
    if not sources:
        sys.exit("error: provide at least one -par (range/file/list) or -w file")

    npay = len(args.pay)
    if len(sources) == 1:
        # One source shared by every payload template.
        return [(v,) * npay for v in sources[0]]
    if len(sources) == npay:
        # One source per payload: zip them row by row.
        lens = [len(s) for s in sources]
        n = min(lens)
        if len(set(lens)) != 1:
            sys.stderr.write(f"[!] sources differ in length {lens}; truncating to {n}\n")
        return [tuple(s[i] for s in sources) for i in range(n)]
    sys.exit(f"error: got {len(sources)} value source(s) but {npay} -pay template(s); "
             f"use 1 shared source or exactly {npay} (one per -pay)")


def substitute(token, mapping):
    def repl(m):
        name = m.group(1) or m.group(2)
        return mapping.get(name, m.group(0))
    return PAY_RE.sub(repl, token)


def run_one(row, args):
    # row holds one value per -pay template. Build $PAY1.., $PAY == $PAY1.
    mapping = {}
    for i, tpl in enumerate(args.pay, 1):
        mapping[f'PAY{i}'] = tpl.replace(args.ph, row[i - 1])
    mapping['PAY'] = mapping['PAY1']
    cmd = [substitute(tok, mapping) for tok in args.cmd]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    label = row[0] if len(row) == 1 else ','.join(row)
    return label, proc.returncode, proc.stdout, proc.stderr


def main():
    args = parse_args(sys.argv[1:])
    rows = build_rows(args)
    total = len(rows)
    if total == 0:
        sys.exit("error: sources produced 0 values")
    sys.stderr.write(f"[*] {total} iterations, {len(args.pay)} payload(s), "
                     f"jobs={args.jobs} -> {args.out}\n")

    done = 0
    errors = 0
    with open(args.out, 'w', encoding='utf-8') as fout:
        def handle(result):
            nonlocal done, errors
            label, rc, out, err = result
            if rc != 0:
                errors += 1
                sys.stderr.write(f"\n[!] value {label!r} exit {rc}: "
                                 f"{err.strip()[:200]}\n")
                if args.stop_on_error:
                    raise SystemExit(f"aborting on error at value {label!r}")
            if done and args.sep:
                fout.write(args.sep)
            fout.write(out)
            done += 1
            if done % 50 == 0 or done == total:
                sys.stderr.write(f"\r[*] {done}/{total}   ")
                sys.stderr.flush()

        if args.jobs > 1:
            with ThreadPoolExecutor(max_workers=args.jobs) as ex:
                # executor.map preserves input order in its output.
                for res in ex.map(lambda r: run_one(r, args), rows):
                    handle(res)
        else:
            for r in rows:
                handle(run_one(r, args))

    sys.stderr.write(f"\n[+] wrote {done} outputs to {args.out}"
                     + (f" ({errors} errors)" if errors else "") + "\n")


if __name__ == '__main__':
    main()
