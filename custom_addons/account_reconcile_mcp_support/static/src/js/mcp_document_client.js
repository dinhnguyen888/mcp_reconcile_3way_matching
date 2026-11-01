/** @odoo-module **/

import { _t } from "@web/core/l10n/translation";

const MCP_ENDPOINTS = [
    "http://127.0.0.1:8785/api/process_statement",
    "http://localhost:8785/api/process_statement",
    "http://127.0.0.1:8785",
    "http://localhost:8785",
];

const MCP_HEALTH_ENDPOINTS = [
    "http://127.0.0.1:8785/health",
    "http://localhost:8785/health",
];

async function hasHealthyMcpClient() {
    for (const url of MCP_HEALTH_ENDPOINTS) {
        try {
            const response = await fetch(url, { method: "GET" });
            if (response.ok) {
                return true;
            }
        } catch {
            // Try the next local endpoint.
        }
    }
    return false;
}

/**
 * Send a document from the browser to the local MCP client.
 *
 * Keeping this call client-side is intentional: 127.0.0.1 is the workstation
 * running the browser, not necessarily the Odoo server.
 */
export async function processDocumentWithLocalMcp({
    fileBase64,
    fileName,
    documentType,
    companyId,
    journalId = null,
    attachmentId = null,
}) {
    if (!fileBase64) {
        throw new Error(_t("Please select a document to scan."));
    }
    let cleanBase64 = String(fileBase64);
    if (cleanBase64.includes(",")) {
        cleanBase64 = cleanBase64.split(",")[1];
    }
    cleanBase64 = cleanBase64.replace(/\s+/g, "");

    if (!(await hasHealthyMcpClient())) {
        throw new Error(
            _t("Could not connect to local MCP at 127.0.0.1:8785. Start mcp_client.py and try again.")
        );
    }

    const payload = {
        file_base64: cleanBase64,
        file: cleanBase64,
        file_name: fileName,
        company_id: companyId,
        journal_id: journalId,
        attachment_id: attachmentId,
        type: documentType,
    };
    let lastError;
    for (const endpoint of MCP_ENDPOINTS) {
        try {
            const response = await fetch(endpoint, {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    "X-Document-Type": documentType,
                },
                body: JSON.stringify(payload),
            });
            if (!response.ok) {
                lastError = new Error(`${response.status} ${response.statusText}`);
                continue;
            }
            const result = await response.json();
            if (!result || result.success === false) {
                throw new Error(result?.error || _t("MCP could not extract this document."));
            }
            return result;
        } catch (error) {
            lastError = error;
        }
    }
    throw lastError || new Error(_t("The local MCP client did not respond."));
}
