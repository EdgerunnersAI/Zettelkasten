---
title: "MarkusPfundstein/mcp-obsidian"
source_type: github
source_url: "https://github.com/MarkusPfundstein/mcp-obsidian"
status: processed
fetch_timestamp: "2026-03-28T00:15:21.629854+00:00"
gemini_tokens_used: 4332
gemini_latency_ms: 14500
tags:
  - "source/github"
  - "domain/AI"
  - "domain/Knowledge Management"
  - "domain/Productivity"
  - "domain/Developer Tools"
  - "domain/Integration"
  - "type/Tool"
  - "type/Configuration Guide"
  - "type/Integration Guide"
  - "difficulty/Intermediate"
  - "status/Processed"
  - "keyword/MCP server"
  - "keyword/Obsidian"
  - "keyword/REST API"
  - "keyword/Claude"
  - "keyword/AI integration"
  - "keyword/Knowledge Graph"
  - "keyword/Python"
  - "keyword/Local API"
metadata:
  owner: "MarkusPfundstein"
  repo: "mcp-obsidian"
  description: "MCP server that interacts with Obsidian via the Obsidian rest API community plugin"
  stars: "3136"
  forks: "370"
  language: "Python"
  created_at: "2024-11-29T11:07:12Z"
  updated_at: "2026-03-27T22:56:27Z"
  license: "MIT"
  open_issues: "79"
  homepage: "None"
---

# MarkusPfundstein/mcp-obsidian

> A Python-based MCP server enabling Claude to interact with Obsidian vaults via the Local REST API plugin, offering tools for file management, content manipulation, and search.

**Source:** [github](https://github.com/MarkusPfundstein/mcp-obsidian)

## Summary

## Project Overview
- **Description**: This project provides an MCP (Model Context Protocol) server designed to interact with Obsidian via the Obsidian Local REST API community plugin.
- **Languages**: Python: 100.0%

## Functionality
- **Tools Implemented**: The server offers multiple tools for Obsidian interaction:
  - `list_files_in_vault`: Lists all files and directories in the root of the Obsidian vault.
  - `list_files_in_dir`: Lists all files and directories within a specified Obsidian directory.
  - `get_file_contents`: Retrieves the content of a single file in the vault.
  - `search`: Searches for documents matching a text query across all files in the vault.
  - `patch_content`: Inserts content into an existing note relative to a heading, block reference, or frontmatter field.
  - `append_content`: Appends content to a new or existing file in the vault.
  - `delete_file`: Deletes a file or directory from the vault.
- **Example Prompts for Claude**: To ensure Claude uses the tool, it's recommended to first instruct it to use Obsidian. Example prompts include:
  - "Get the contents of the last architecture call note and summarize them"
  - "Search for all files where Azure CosmosDb is mentioned and quickly explain to me the context in which it is mentioned"
  - "Summarize the last meeting notes and put them into a new note 'summary meeting.md'. Add an introduction so that I can send it via email."

## Configuration
- **Obsidian REST API Key**: Required for server operation.
  - The API key can be found in the Obsidian plugin configuration.
  - Default port is 27124 if not specified.
  - Default host is 127.0.0.1 if not specified.
- **Configuration Methods**:
  - **1. Add to server config (preferred)**:
    ```json
    {
      "mcp-obsidian": {
        "command": "uvx",
        "args": [
          "mcp-obsidian"
        ],
        "env": {
          "OBSIDIAN_API_KEY": "<your_api_key_here>",
          "OBSIDIAN_HOST": "<your_obsidian_host>",
          "OBSIDIAN_PORT": "<your_obsidian_port>"
        }
      }
    }
    ```
    - Note: If Claude has issues detecting `uv`/`uvx`, use `which uvx` to find and paste the full path.
  - **2. Create a `.env` file**: Place a `.env` file in the working directory with the following variables:
    ```
    OBSIDIAN_API_KEY=your_api_key_here
    OBSIDIAN_HOST=your_obsidian_host
    OBSIDIAN_PORT=your_obsidian_port
    ```

## Quickstart
- **Obsidian REST API Plugin**: Install and enable the `obsidian-local-rest-api` community plugin from `https://github.com/coddingtonbear/obsidian-local-rest-api` and copy the API key.
- **Claude Desktop Configuration**:
  - **Configuration File Paths**:
    - MacOS: `~/Library/Application\ Support/Claude/claude_desktop_config.json`
    - Windows: `%APPDATA%/Claude/claude_desktop_config.json`
  - **Development/Unpublished Servers Configuration Example**:
    ```json
    {
      "mcpServers": {
        "mcp-obsidian": {
          "command": "uv",
          "args": [
            "--directory",
            "<dir_to>/mcp-obsidian",
            "run",
            "mcp-obsidian"
          ],
          "env": {
            "OBSIDIAN_API_KEY": "<your_api_key_here>",
            "OBSIDIAN_HOST": "<your_obsidian_host>",
            "OBSIDIAN_PORT": "<your_obsidian_port>"
          }
        }
      }
    }
    ```
  - **Published Servers Configuration Example**:
    ```json
    {
      "mcpServers": {
        "mcp-obsidian": {
          "command": "uvx",
          "args": [
            "mcp-obsidian"
          ],
          "env": {
            "OBSIDIAN_API_KEY": "<YOUR_OBSIDIAN_API_KEY>",
            "OBSIDIAN_HOST": "<your_obsidian_host>",
            "OBSIDIAN_PORT": "<your_obsidian_port>"
          }
        }
      }
    }
    ```

## Development
- **Building**: To prepare the package for distribution, run `uv sync` to sync dependencies and update the lockfile.
- **Debugging**: 
  - **MCP Inspector**: Recommended for the best debugging experience.
    - Launch with `npx @modelcontextprotocol/inspector uv --directory /path/to/mcp-obsidian run mcp-obsidian`.
    - The Inspector will display a URL for browser-based debugging.
  - **Server Logs**: Watch server logs using `tail -n 20 -f ~/Library/Logs/Claude/mcp-server-mcp-obsidian.log`.

## Related Notes

- [[github_2026-03-27_jina-aireader]]
- [[newsletter_2026-03-27_is-something-bugging-you]]
- [[youtube_2026-03-27_notebooklm-changed-completely-heres-what-matters-in-2026]]
