/** @odoo-module **/

import { FormController } from "@web/views/form/form_controller";
import { formView } from "@web/views/form/form_view";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";

const MCP_ENDPOINTS = [
    "http://127.0.0.1:8765/api/process_statement",
    "http://localhost:8765/api/process_statement",
    "http://127.0.0.1:8765",
    "http://localhost:8765",
];
const MCP_HEALTH = ["http://127.0.0.1:8765/health", "http://localhost:8765/health"];

function isSizeString(val) {
    return typeof val === "string" && (
        /^\s*\d+(\.\d+)?\s*(bytes|b|kb|mb|gb)\s*$/i.test(val) ||
        (val.length < 100 && /(kb|mb|bytes)/i.test(val))
    );
}

function many2oneId(value) {
    if (Array.isArray(value)) {
        return value[0];
    }
    return value || null;
}

export class StatementUploadWizardController extends FormController {
    setup() {
        super.setup(...arguments);
        this.notification = useService("notification");
        this.companyService = useService("company");
        this.actionService = useService("action");
        this.orm = useService("orm");
    }

    async beforeExecuteActionButton(clickParams) {
        if (clickParams.name !== "action_process") {
            return super.beforeExecuteActionButton(clickParams);
        }
        try {
            let initialDoc = this.model.root.data.document;
            await this.model.root.save();

            let actualBase64 = initialDoc;
            if (!actualBase64 || isSizeString(actualBase64) || String(actualBase64).length < 100) {
                if (this.model.root.resId) {
                    const [rec] = await this.orm.read(
                        "account.statement.upload.wizard",
                        [this.model.root.resId],
                        ["document", "file_name", "journal_id", "attachment_id", "document_type", "move_id"],
                        { load: false }
                    );
                    if (rec && rec.document && !isSizeString(rec.document)) {
                        actualBase64 = rec.document;
                    } else if (rec && rec.attachment_id) {
                        const attId = many2oneId(rec.attachment_id);
                        const [att] = await this.orm.read("ir.attachment", [attId], ["datas"]);
                        if (att && att.datas) {
                            actualBase64 = att.datas;
                        }
                    }
                }
            }

            const docType = this.model.root.data.document_type || "bank_statement";
            const mcpResult = await this.pressProcess(clickParams, actualBase64, docType);

            if (docType === "vendor_bill") {
                if (!mcpResult || mcpResult.success === false) {
                    this.notification.add(
                        _t("Local MCP did not return a scanned invoice. Start mcp_client.py on this computer (port 8765) and try again."),
                        { type: "danger", sticky: true }
                    );
                    return false;
                }
                const action = await this.orm.call(
                    "account.statement.upload.wizard",
                    "action_apply_vendor_bill",
                    [this.model.root.resId],
                    { mcp_data: mcpResult }
                );
                if (action) {
                    await this.actionService.doAction(action);
                    return false;
                }
                return false;
            }

            const action = await this.orm.call(
                "account.statement.upload.wizard",
                "action_open_preview",
                [this.model.root.resId],
                { mcp_data: mcpResult }
            );
            if (action) {
                await this.actionService.doAction(action);
                return false;
            }
        } catch (err) {
            console.warn("[StatementUploadWizard] Error after MCP scan:", err);
            this.notification.add(
                (err && (err.data?.message || err.message)) || _t("Could not process the document."),
                { type: "danger" }
            );
            return false;
        }
        return super.beforeExecuteActionButton(clickParams);
    }

    async pressProcess(clickParams = {}, explicitBase64 = null, docType = "bank_statement") {
        const rootData = this.model.root.data;
        let fileBase64 = explicitBase64 || rootData.document;
        const fileName = rootData.file_name || "uploaded_document.pdf";
        const companyId = this.companyService?.currentCompany?.id || 1;
        const journalId = many2oneId(rootData.journal_id);
        const attachmentId = many2oneId(rootData.attachment_id);

        if (!fileBase64 || isSizeString(fileBase64)) {
            this.notification.add(_t("Please upload a bank statement or invoice file before processing."), {
                type: "warning",
            });
            return null;
        }

        let cleanBase64 = String(fileBase64);
        if (cleanBase64.includes(",")) {
            cleanBase64 = cleanBase64.split(",")[1];
        }
        cleanBase64 = cleanBase64.replace(/\s+/g, "");

        const payload = {
            file_base64: cleanBase64,
            file: cleanBase64,
            file_name: fileName,
            company_id: companyId,
            journal_id: journalId,
            attachment_id: attachmentId,
            type: docType,
        };

        this.notification.add(
            _t("Sending document to local AI (this may take a few seconds)..."),
            { type: "info", sticky: false }
        );

        try {
            const healthy = await this._mcpHealth();
            if (!healthy) {
                throw new Error("MCP healthcheck failed");
            }

            let response = null;
            let lastError = null;
            for (const endpoint of MCP_ENDPOINTS) {
                try {
                    response = await fetch(endpoint, {
                        method: "POST",
                        headers: {
                            "Content-Type": "application/json",
                            "X-Document-Type": docType,
                        },
                        body: JSON.stringify(payload),
                    });
                    if (response.ok) {
                        break;
                    }
                } catch (err) {
                    lastError = err;
                }
            }

            if (!response || !response.ok) {
                throw lastError || new Error(_t("MCP server at 127.0.0.1:8765 did not respond."));
            }

            const result = await response.json();
            if (result.success) {
                this.agyResult = result.result || result;
                this.notification.add(result.message || _t("Document analyzed by local AI."), {
                    type: "success",
                    sticky: false,
                });
                return result;
            }
            this.notification.add(result.error || _t("MCP returned an error."), { type: "warning" });
            return result;
        } catch (error) {
            console.warn("[StatementUploadWizard] MCP unavailable:", error);
            this.notification.add(
                _t("Could not connect to local MCP at 127.0.0.1:8765. Start mcp_client.py on this computer."),
                { type: docType === "vendor_bill" ? "danger" : "warning", sticky: docType === "vendor_bill" }
            );
            return null;
        }
    }

    async _mcpHealth() {
        for (const url of MCP_HEALTH) {
            try {
                const res = await fetch(url, { method: "GET" });
                if (res.ok) {
                    return true;
                }
            } catch {
                // try next
            }
        }
        return false;
    }
}

export const statementUploadWizardView = {
    ...formView,
    Controller: StatementUploadWizardController,
};

registry.category("views").add("statement_upload_wizard", statementUploadWizardView);
