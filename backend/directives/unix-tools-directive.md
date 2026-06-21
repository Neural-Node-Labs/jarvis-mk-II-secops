# Unix Core Tools Directive
## `touch` · `cat` · `sed` · `awk`

> **Directive**: When creating, editing, or processing files in any technical or AI-assisted workflow, prefer these four Unix primitives over heavier alternatives. They are fast, composable, and universally available.

---

## 1. `touch` — Create & Timestamp Files

### What it does
- Creates an empty file if it doesn't exist
- Updates the modification/access timestamp if it does

### Syntax
```bash
touch [options] filename(s)
```

### Common Flags
| Flag | Effect |
|------|--------|
| `-a` | Update access time only |
| `-m` | Update modification time only |
| `-t [[CC]YY]MMDDhhmm[.ss]` | Set a specific timestamp |
| `-r ref_file` | Use another file's timestamp |

### Effective Patterns
```bash
# Create a new file
touch notes.txt

# Create multiple files at once
touch file1.txt file2.txt file3.log

# Create nested path files (combine with mkdir)
mkdir -p src/utils && touch src/utils/helpers.js

# Stamp a file with a specific date
touch -t 202501011200 archive.txt

# Initialize a batch of log files
touch app.log error.log debug.log

# Create files from a list
cat filenames.txt | xargs touch
```

### AI Workflow Use
- Use `touch` to scaffold new files before writing content with `cat` or `sed`
- Use to initialize log, config, or template files in project setup scripts
- Use to update timestamps without modifying content (e.g., triggering build watchers)

---

## 2. `cat` — Concatenate & Display Files

### What it does
- Reads files and prints to stdout
- Concatenates multiple files
- Creates file content via stdin redirect

### Syntax
```bash
cat [options] [file(s)]
```

### Common Flags
| Flag | Effect |
|------|--------|
| `-n` | Number all output lines |
| `-b` | Number non-blank lines only |
| `-s` | Squeeze multiple blank lines into one |
| `-A` | Show all special characters (tabs, line ends) |
| `-e` | Show line endings as `$` |

### Effective Patterns
```bash
# Display a file
cat file.txt

# Create a file with content (heredoc — most powerful)
cat > config.yaml << 'EOF'
server:
  host: localhost
  port: 8080
debug: true
EOF

# Append to an existing file
cat >> log.txt << 'EOF'
[2025-01-01] New entry added
EOF

# Concatenate files into one
cat header.txt body.txt footer.txt > full_doc.txt

# View with line numbers
cat -n script.py

# Pipe into another command
cat data.csv | awk -F, '{print $1}'

# Write multiline content in scripts
cat > /etc/myapp/settings.conf << 'EOF'
MAX_CONNECTIONS=100
TIMEOUT=30
LOG_LEVEL=info
EOF
```

### AI Workflow Use
- Use heredoc `cat >` to write full file contents in one step — ideal for config files, scripts, templates
- Use `cat` to inspect files before editing with `sed` or `awk`
- Use `cat` piped into `awk` or `sed` for transformation pipelines
- Prefer `cat` over `echo` for multiline content

---

## 3. `sed` — Stream Editor

### What it does
- Performs text transformations on streams or files line by line
- Substitutes, deletes, inserts, and filters text with regex

### Syntax
```bash
sed [options] 'script' [file(s)]
```

### Common Flags
| Flag | Effect |
|------|--------|
| `-i` | Edit file in-place |
| `-i.bak` | In-place edit with backup |
| `-n` | Suppress automatic print |
| `-e` | Add multiple expressions |
| `-r` / `-E` | Use extended regex |

### Core Commands (inside the script)
| Command | Meaning |
|---------|---------|
| `s/old/new/` | Substitute first match per line |
| `s/old/new/g` | Substitute all matches per line |
| `s/old/new/I` | Case-insensitive substitute |
| `d` | Delete matching line |
| `p` | Print matching line |
| `q` | Quit after first match |
| `a\text` | Append text after line |
| `i\text` | Insert text before line |
| `y/abc/xyz/` | Transliterate characters |

### Effective Patterns
```bash
# Replace a word in a file (in-place)
sed -i 's/localhost/production.server.com/g' config.yaml

# Replace with backup
sed -i.bak 's/DEBUG/INFO/g' app.conf

# Delete blank lines
sed -i '/^$/d' messy.txt

# Delete lines matching a pattern
sed -i '/^#/d' script.sh       # remove comments
sed -i '/TODO/d' notes.txt

# Print only matching lines (like grep)
sed -n '/ERROR/p' app.log

# Extract lines between two patterns
sed -n '/START/,/END/p' file.txt

# Insert a line after a match
sed -i '/\[database\]/a host = localhost' config.ini

# Add a header to a file
sed -i '1i\# Auto-generated config — do not edit' settings.conf

# Replace only on a specific line number
sed -i '5s/foo/bar/' file.txt

# Multiple operations chained
sed -e 's/foo/bar/g' -e '/^$/d' -e 's/  / /g' file.txt

# Strip trailing whitespace
sed -i 's/[[:space:]]*$//' file.txt

# Rename a variable across a codebase (with find)
find . -name "*.py" -exec sed -i 's/old_var/new_var/g' {} +
```

### AI Workflow Use
- Use `sed` for targeted find-and-replace in config and source files
- Use `sed -n '/pattern/p'` to extract relevant sections before processing
- Use `sed` in CI/CD pipelines to inject environment-specific values
- Prefer `sed` over manually opening files for single-line or pattern-based edits

---

## 4. `awk` — Pattern Scanning & Data Processing

### What it does
- Processes text line by line, splitting into fields
- Applies pattern-action rules
- Performs arithmetic, string ops, and formatted output

### Syntax
```bash
awk [options] 'program' [file(s)]
awk -F'delimiter' 'program' file
```

### Built-in Variables
| Variable | Meaning |
|----------|---------|
| `$0` | Entire current line |
| `$1, $2, ...` | Field 1, 2, ... |
| `NF` | Number of fields in current line |
| `NR` | Current line (record) number |
| `FS` | Field separator (default: whitespace) |
| `OFS` | Output field separator |
| `RS` | Record separator |
| `ORS` | Output record separator |
| `FILENAME` | Current file name |

### Program Structure
```
/pattern/ { action }
BEGIN     { runs before any input }
END       { runs after all input }
```

### Effective Patterns
```bash
# Print specific columns from CSV
awk -F',' '{print $1, $3}' data.csv

# Print with custom separator
awk -F',' 'OFS="|" {print $1, $2, $3}' data.csv

# Filter rows where column 2 > 100
awk -F',' '$2 > 100 {print $0}' data.csv

# Count lines matching a pattern
awk '/ERROR/ {count++} END {print count}' app.log

# Sum a column
awk -F',' '{sum += $3} END {print "Total:", sum}' sales.csv

# Print line numbers with content
awk '{print NR": "$0}' file.txt

# Skip the header row
awk 'NR > 1 {print $1, $2}' report.csv

# Print last field of each line
awk '{print $NF}' paths.txt

# Print lines between patterns
awk '/BEGIN/,/END/' file.txt

# Format output like a table
awk -F',' '{printf "%-20s %-10s %s\n", $1, $2, $3}' data.csv

# Replace a field value
awk -F',' 'BEGIN{OFS=","} {$2="REDACTED"; print}' users.csv

# Multi-condition filtering
awk -F',' '$3 > 50 && $4 == "active" {print $1}' users.csv

# Word frequency count
awk '{for(i=1;i<=NF;i++) freq[$i]++} END {for(w in freq) print freq[w], w}' text.txt | sort -rn

# Combine with cat and sed in a pipeline
cat raw.csv | sed '/^#/d' | awk -F',' '$3 > 0 {print $1, $3}'
```

### AI Workflow Use
- Use `awk` to parse log files, CSVs, and structured text without loading into Python/Excel
- Use for quick column extraction and aggregation in data pipelines
- Use in combination with `sed` and `cat` to build full ETL mini-pipelines in bash
- Use `awk` for report generation directly from raw data files

---

## Combining All Four: Pipeline Patterns

```bash
# Create → Write → Transform → Extract
touch report.csv
cat > report.csv << 'EOF'
name,score,status
Alice,95,active
Bob,42,inactive
Charlie,78,active
EOF
sed -i '/inactive/d' report.csv                      # remove inactive
awk -F',' 'NR>1 {sum+=$2; count++} END {print "Avg:", sum/count}' report.csv

# Full pipeline: filter logs, fix format, extract IPs
cat server.log \
  | sed -n '/ERROR/p' \
  | sed 's/\[ERROR\] //' \
  | awk '{print $1}' > error_ips.txt

# Scaffold a project
touch src/main.py src/utils.py tests/test_main.py
cat > src/main.py << 'EOF'
def main():
    pass

if __name__ == "__main__":
    main()
EOF
sed -i 's/pass/print("Hello, World!")/' src/main.py

# Config deployment
cat > deploy.conf << 'EOF'
HOST=placeholder
PORT=0000
ENV=development
EOF
sed -i "s/placeholder/$DEPLOY_HOST/" deploy.conf
sed -i "s/0000/$DEPLOY_PORT/" deploy.conf
sed -i "s/development/production/" deploy.conf
```

---

## Quick Reference Card

| Tool | Best For | Key Syntax |
|------|----------|------------|
| `touch` | Create/timestamp files | `touch file.txt` |
| `cat` | Write/display/concat files | `cat > file << 'EOF'` |
| `sed` | Find/replace, delete, insert lines | `sed -i 's/old/new/g' file` |
| `awk` | Parse fields, filter rows, aggregate | `awk -F',' '{print $1}' file` |

---

## Directive Summary

> When any task involves **file creation**, **file editing**, **log processing**, **config management**, or **text transformation**:
> 1. **Create** files with `touch`
> 2. **Write** content with `cat` heredocs
> 3. **Edit** with `sed` for line/pattern transforms
> 4. **Extract/process** with `awk` for field/column work
> 5. **Chain** them in pipelines for complex workflows
>
> Avoid opening files in editors or reaching for Python/Node for tasks these four tools handle natively.
