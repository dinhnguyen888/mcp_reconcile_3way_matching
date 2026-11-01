/** @odoo-module **/

import { FormController } from "@web/views/form/form_controller";
import { formView } from "@web/views/form/form_view";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";
import { processDocumentWithLocalMcp } from "./mcp_document_client";

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

            if (docType === "vendor_bill" || docType === "receipt") {
                if (!mcpResult || mcpResult.success === false) {
                    this.notification.add(
                        _t("Local MCP did not return a scanned invoice. Start mcp_client.py on this computer (port 8785) and try again."),
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

        this.notification.add(
            _t("Sending document to local AI (this may take a few seconds)..."),
            { type: "info", sticky: false }
        );

        try {
            const result = await processDocumentWithLocalMcp({
                fileBase64: cleanBase64,
                fileName,
                documentType: docType,
                companyId,
                journalId,
                attachmentId,
            });
            this.agyResult = result.result || result;
            this.notification.add(result.message || _t("Document analyzed by local AI."), {
                type: "success",
                sticky: false,
            });
            return result;
        } catch (error) {
            console.warn("[StatementUploadWizard] MCP unavailable:", error);
            this.notification.add(
                _t("Could not connect to local MCP at 127.0.0.1:8785. Start mcp_client.py on this computer."),
                { type: docType === "bank_statement" ? "warning" : "danger", sticky: docType !== "bank_statement" }
            );
            return null;
        }
    }
}

export const statementUploadWizardView = {
    ...formView,
    Controller: StatementUploadWizardController,
};

registry.category("views").add("statement_upload_wizard", statementUploadWizardView);
