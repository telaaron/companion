# Plugins

Drop a YAML file here to register additional tools. The proxy scans this
directory at startup; restart after adding a plugin.

## Obsidian vault

```yaml
name: my-vault
kind: obsidian_vault
vault_path: /Users/you/Documents/Obsidian Vault
```

Exposes:

- `Obsidian<Name>List(folder?)` — list notes in the vault (sorted by mtime)
- `Obsidian<Name>Read(path)` — read a note by vault-relative path
- `Obsidian<Name>Append(path, content)` — append content (creates the file
  if missing)

`<Name>` is derived from the plugin's `name` field (alphanumerics only).

## MCP server (roadmap)

```yaml
name: linear
kind: mcp_server
command: npx
args: -y @modelcontextprotocol/server-linear
```

Currently a placeholder — subprocess + JSON-RPC plumbing is on the roadmap.
