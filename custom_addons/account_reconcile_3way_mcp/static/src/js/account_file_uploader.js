/** @odoo-module **/

import {
    AccountFileUploader,
    accountFileUploader,
} from "@account/components/account_file_uploader/account_file_uploader";
import { processDocumentWithLocalMcp } from "@account_reconcile_mcp_support/js/mcp_document_client";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

export class ThreeWayMcpAccountFileUploader extends AccountFileUploader {
    static props = {
        ...AccountFileUploader.props,
        jsClass: { type: String, optional: true },
    };

    setup() {
        super.setup(...arguments);
        this.company = useService("company");
        this.pendingMcpUploads = [];
    }

    async onFileUploaded(file) {
        const journalId = this.props.record?.data?.id;
        const companyId = this.company.currentCompany.id;
        this.notification.add(_t("Scanning vendor bill with local MCP..."), {
            type: "info",
        });
        const mcpData = await processDocumentWithLocalMcp({
            fileBase64: file.data,
            fileName: file.name,
            documentType: "vendor_bill",
            companyId,
            journalId,
        });

        const cleanContext = Object.fromEntries(
            Object.entries(this.env.searchModel.context).filter(
                ([key]) => !key.startsWith("default_")
            )
        );
        const [attachmentId] = await this.orm.create(
            "ir.attachment",
            [{ name: file.name, mimetype: file.type, datas: file.data }],
            { context: cleanContext }
        );
        this.pendingMcpUploads.push({
            attachment_id: attachmentId,
            mcp_data: mcpData,
        });
    }

    async onUploadComplete() {
        const journalId = this.props.record?.data?.id;
        const uploads = this.pendingMcpUploads;
        this.pendingMcpUploads = [];
        if (!journalId || !uploads.length) {
            return;
        }
        const action = await this.orm.call(
            "account.journal",
            "process_mcp_vendor_bill_uploads",
            [[journalId], uploads]
        );
        this.notification.add(_t("Vendor bill mapped to three-way matching."), {
            type: "success",
        });
        if (action) {
            await this.action.doAction(action);
        }
    }
}

export const threeWayMcpAccountFileUploader = {
    ...accountFileUploader,
    component: ThreeWayMcpAccountFileUploader,
    extractProps: (args) => ({
        ...accountFileUploader.extractProps(args),
        jsClass: args.attrs.js_class,
    }),
};

registry
    .category("view_widgets")
    .add("account_reconcile_3way_mcp_file_uploader", threeWayMcpAccountFileUploader);
