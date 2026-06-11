

# Jarvis Experienced Notes

## File Writing Protocol (CRITICAL)

### The Golden Rule
When writing ANY file, **do NOT pass file content through your LLM output window**.
Your output token limit (~8KB) will truncate large content and the tool call will fail silently.
Instead, use these methods in order of preference:

### Method 1: os_execution (PREFERRED for large files)
Write content directly to disk via shell commands, bypassing your token limit entirely:
```
TOOL_CALL: {"skill": "os_execution", "action": "run_command", "params": {
  "command": "cat > /path/to/file << 'ENDOFFILE'\n...content...\nENDOFFILE"
}}
```

### Method 2: file_streamer.write_file (for moderate files < 8KB)
Single-shot atomic write with MD5 validation:
```
TOOL_CALL: {"skill": "file_streamer", "action": "write_file", "params": {
  "path": "/path/to/file",
  "content": "...content...",
  "overwrite": true
}}
```

### Method 3: file_streamer chunked path (for files needing multi-turn assembly)
ONLY use start_file → append_chunk → finalize_file when:
- Content is being generated across multiple LLM turns
- Each individual chunk fits within your output window (< 4KB per chunk)

### Method 4: filesystem.write_file (last resort)
Only for tiny files under 2KB where the above methods fail.

### Critical Parameters
- `file_streamer` uses `path` (NOT `filepath`)
- `file_streamer` actions: `write_file`, `start_file`, `append_chunk`, `finalize_file`, `status`, `abort`
- For chunked writes: `start_file` → `append_chunk` (×N) → `finalize_file`
- `write_file` handles atomic temp + replace + MD5 automatically
