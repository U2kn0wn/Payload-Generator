# payload_generator

Run **any** single-input command in a loop, mutating its input on every
iteration, and save each raw output to a file.

It exists to wrap tools that don't accept files or multiple inputs natively.
Instead of writing a one-off `for` loop every time, you point `payload_gen` at a
value source (a numeric range, a wordlist file, or an inline list), drop a
placeholder into a payload template, and it fires your command once per value —
collecting every output in order.

## Features

- **Any command** — runs your command directly (no shell), so JSON, quotes, and
  special characters in the payload are never mangled.
- **Three value sources** — a numeric range, a file (one value per line), or an
  inline comma-separated list. Auto-detected.
- **Multiple payloads** — inject several independent values per iteration via
  `$PAY1`, `$PAY2`, … (`$PAY` is an alias for `$PAY1`).
- **Zipped sources** — give each payload its own source and iterate them
  row-by-row (e.g. user/password pairs).
- **Parallel** — `-j N` workers; output is always written in input order.
- **Clean output** — only the command's raw stdout goes to the file; nothing is
  added. Progress and errors go to stderr.

## Requirements

- Python 3.6+ (standard library only)

## Install

```bash
git clone <your-repo-url>
cd <repo>
chmod +x payload_gen.py
```

## Usage

```
payload_gen.py -pay TEMPLATE [-par SOURCE] [-w FILE] -o OUTFILE
               [-j N] [--ph CHAR] [--sep STR] [--stop-on-error]
               -cmd COMMAND ...
```

Everything after `-cmd` is the command to run. It **must come last**. The
placeholder (default `*`) inside each `-pay` template is replaced by the current
value, and the result is exposed to the command as `$PAY` / `$PAY1` / `$PAY2` …

> Quote `'$PAY'` in your shell so *your* shell doesn't expand it — `payload_gen`
> does the substitution itself.

### Value sources

A `-par` (or `-w`) source can be any of:

| Type  | Example              | Meaning                              |
|-------|----------------------|--------------------------------------|
| Range | `-par 1,1000`        | `start,end` — end inclusive          |
| Range | `-par 1,1000,5`      | `start,end,step`                     |
| File  | `-par values.txt`    | one value per line                   |
| List  | `-par a,b,c`         | inline comma-separated values        |

Detection order: an existing file path is read as a **file**; otherwise a
`start,end[,step]` of integers is a **range**; anything else is a **list**.
Use `-w FILE` to force file mode when a filename would otherwise look like a
range or list.

## Options

| Flag              | Description                                                        |
|-------------------|-------------------------------------------------------------------|
| `-pay TEMPLATE`   | Payload template with a placeholder. Repeatable → `$PAY1,$PAY2,…` |
| `-par SOURCE`     | Value source: range, file path, or inline list. Repeatable.       |
| `-w FILE`         | Value source forced to be a file. Repeatable.                     |
| `-o FILE`         | Output file (raw stdout only).                                    |
| `-j N`            | Parallel workers (default `1`).                                   |
| `--ph CHAR`       | Placeholder string (default `*`).                                 |
| `--sep STR`       | Separator written between outputs (default: none).                |
| `--stop-on-error` | Abort if a command exits non-zero (default: keep going).          |
| `-cmd COMMAND …`  | The command to run each iteration. Must come last.                |
| `-h`, `--help`    | Show help.                                                        |

## Examples

Run a command over a numeric range:

```bash
./payload_gen.py -pay '{"id":"*"}' -par 1,1000 -o out.txt \
    -cmd curl -s https://target/api -d '$PAY'
```

Drive a tool that has no file support from a wordlist, 8 in parallel:

```bash
./payload_gen.py -pay '*' -par words.txt -j 8 -o out.txt \
    -cmd some-tool --input '$PAY'
```

Use an inline list of values:

```bash
./payload_gen.py -pay 'role=*' -par admin,root,manager -o out.txt \
    -cmd curl -s https://target/x -d '$PAY'
```

Two payloads, each from its own file, zipped row-by-row (e.g. credential pairs):

```bash
./payload_gen.py -pay 'user=*' -pay 'pass=*' \
    -par users.txt -par passwords.txt -o out.txt \
    -cmd curl -s https://target/login --data '$PAY1&$PAY2'
```

Change the placeholder when `*` collides with your data:

```bash
./payload_gen.py -pay 'SELECT * FROM t WHERE id=@' -par 1,50 --ph @ -o out.txt \
    -cmd mytool --query '$PAY'
```

## How output is written

- Each command's **stdout** is appended to the output file exactly as produced —
  no separators, headers, or newlines are added (use `--sep` if you want a
  separator between entries).
- With `-j 1` output is streamed straight to disk. With `-j N` it is collected
  and written in the original input order.
- Failures (non-zero exit) are reported on stderr; the run continues unless
  `--stop-on-error` is set.

## License

MIT
