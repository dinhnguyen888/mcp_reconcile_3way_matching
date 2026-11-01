# MCP Service Setup & Configuration

This guide provides instructions for deploying, configuring, and operating the **Model Context Protocol (MCP) Document Extraction Service** (`mcp_client.py`) bundled with `account_reconcile_mcp_support`.

---

## 1. Overview

The MCP extraction service acts as an intelligent document parsing bridge between Odoo and AI models (such as Google Gemini and Anthropic Claude via the `agy` CLI).

### Key Processing Capabilities:
- **Heuristic Table Parser**: Fast, cost-free extraction of digital PDFs, CSV, and XLSX bank statements using keyword detection and column mapping.
- **Vision OCR & AI Extraction**: For scanned invoices, photographed receipts, or intricate bank statements, documents are chunked and processed in parallel across worker threads.
- **Structured Schema Enforcement**: Returns normalized JSON objects containing transaction dates, description labels, debits, credits, partner information, and VAT numbers.

---

## 2. Environment Variables & Configuration

The service is configured via standard environment variables:

| Variable | Default Value | Description |
| :--- | :--- | :--- |
| `MCP_HOST` | `127.0.0.1` | Host address to bind the HTTP server. |
| `MCP_PORT` | `8785` | Port number for the HTTP/SSE server. |
| `AGY_BIN` | `agy` | Path or name of the Antigravity CLI binary. |
| `AGY_EXTRA_PATH` | `/home/dinh/.local/bin` | Additional PATH prepended to locate the `agy` binary. |
| `AGY_MODEL` | `gemini-3.8-flash-low` | AI model identifier (e.g. `gemini-2.5-pro`, `gemini-3.8-flash`). |
| `AGY_EFFORT` | `low` | Model reasoning effort (`low`, `medium`, `high`). |
| `MAX_TEXT_CHARS` | `15000` | Character threshold per AI text chunk. |
| `AI_WORKERS` | `3` | Number of concurrent worker threads for parallel batch extraction. |
| `SCAN_BATCH` | `4` | Number of scanned statement pages per AI call. |
| `BILL_MAX_PAGES` | `10` | Maximum pages processed in a single vendor bill vision call. |
| `BILL_VISION_TIMEOUT`| `180` | Timeout in seconds for bill vision analysis. |
| `MAX_UPLOAD_MB` | `80` | Maximum file upload size in megabytes. |

---

## 3. Running the Service

### 3.1. Manual Execution (Development / Debugging)

```bash
cd custom_addons/account_reconcile_mcp_support/client
python3 mcp_client.py
```

Console output upon successful startup:
```text
[INFO] Local Document Extraction Service starting on http://127.0.0.1:8785
[INFO] Model: gemini-3.8-flash-low | Workers: 3 | Batch: 4 pages
```

### 3.2. Running as a Systemd Service (Production)

Create a systemd unit file at `/etc/systemd/system/odoo-mcp.service`:

```ini
[Unit]
Description=Odoo MCP Document Extraction Service
After=network.target

[Service]
Type=simple
User=dinh
WorkingDirectory=/home/dinh/workspaces/mcp_reconcile_&&_3way_mathcing/custom_addons/account_reconcile_mcp_support/client
Environment=MCP_PORT=8785
Environment=AGY_MODEL=gemini-2.5-pro
Environment=PATH=/home/dinh/.local/bin:/usr/local/bin:/usr/bin
ExecStart=/usr/bin/python3 mcp_client.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Enable and start the service:
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now odoo-mcp
sudo systemctl status odoo-mcp
```

---

## 4. Testing Service Health & Extraction

### Health Check:
```bash
curl -s http://127.0.0.1:8785/health
```
Expected response:
```json
{"status": "ok", "service": "odoo-document-mcp", "port": 8785}
```

### Testing Document Extraction:
```bash
curl -X POST http://127.0.0.1:8785/extract_document_data \
  -H "Content-Type: application/json" \
  -d '{
    "filename": "sample_invoice.pdf",
    "document_type": "vendor_bill",
    "file_base64": "<BASE64_STRING>"
  }'
```

---

## 5. Odoo Connection Configuration

In Odoo, navigate to **Settings > Technical > Parameters > System Parameters**:
- `account_reconcile_mcp.endpoint`: `http://127.0.0.1:8785`
- `account_reconcile_mcp.timeout`: `180`
