/** @odoo-module **/

import { Component, useState, onMounted, useRef } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";

export class StatementPreviewDialog extends Component {
    static template = "account_reconcile_mcp_support.StatementPreviewDialog";
    static props = {
        action: { type: Object, optional: true },
        "*": true,
    };

    setup() {
        this.actionService = useService("action");
        this.notification = useService("notification");
        this.orm = useService("orm");

        const params = this.props.action?.params || {};
        const records = (params.records || []).map((r, i) => ({
            ...r,
            temp_id: r.temp_id || `rec_${i + 1}`,
            amount: typeof r.amount === "number" ? r.amount : parseFloat(r.amount) || 0.0,
            page_number: r.page_number || 1,
        }));

        this.previewId = params.preview_id || null;
        this.attachmentId = params.attachment_id || null;
        this.journalId = params.journal_id || null;
        this.journalName = params.journal_name || _t("Bank Journal");
        this.fileName = params.file_name || "document.pdf";
        this.engine = params.engine || "gemini-2.5-pro";
        this.message = params.message || "";
        this.pdfUrl = params.pdf_url || "";

        this.docViewerRef = useRef("docViewer");

        this.state = useState({
            records: records,
            selectedTempId: records.length > 0 ? records[0].temp_id : null,
            filterText: "",
            currentPage: 1,
            totalPages: Math.max(
                1,
                params.total_pages || 1,
                ...records.map((r) => r.page_number || 1),
                (params.page_images || []).length
            ),
            pageImages: params.page_images || [],
            zoom: 100,
            isSaving: false,
        });
    }

    get filteredRecords() {
        if (!this.state.filterText) {
            return this.state.records;
        }
        const q = this.state.filterText.toLowerCase().trim();
        return this.state.records.filter((r) => {
            return (
                (r.partner_name || "").toLowerCase().includes(q) ||
                (r.narration || "").toLowerCase().includes(q) ||
                (r.ref || "").toLowerCase().includes(q) ||
                String(r.amount).includes(q) ||
                (r.date || "").includes(q)
            );
        });
    }

    get globalBalance() {
        return this.state.records.reduce((sum, r) => sum + (parseFloat(r.amount) || 0), 0);
    }

    get globalBalanceStr() {
        return this.formatCurrency(this.globalBalance);
    }

    get totalCredit() {
        return this.state.records
            .filter((r) => r.amount > 0)
            .reduce((sum, r) => sum + (parseFloat(r.amount) || 0), 0);
    }

    get totalDebit() {
        return this.state.records
            .filter((r) => r.amount < 0)
            .reduce((sum, r) => sum + Math.abs(parseFloat(r.amount) || 0), 0);
    }

    get renderedPageWidth() {
        if (this.state.zoom === 100) {
            return "100%";
        }
        return `${this.state.zoom}%`;
    }

    get currentPageImageSrc() {
        if (this.state.pageImages && this.state.pageImages.length >= this.state.currentPage) {
            return this.state.pageImages[this.state.currentPage - 1];
        }
        if (this.attachmentId) {
            return `/account_reconcile_mcp_support/page_image/${this.attachmentId}/${this.state.currentPage}`;
        }
        return null;
    }

    get currentPageRecords() {
        return this.state.records.filter((r) => (r.page_number || 1) === this.state.currentPage);
    }

    formatCurrency(val) {
        if (val === null || val === undefined) return "0 ₫";
        const num = parseFloat(val);
        return `${num.toLocaleString("vi-VN")} ₫`;
    }

    /**
     * When clicking a record card on the left:
     * - Highlight card (active-card).
     * - Auto jump to corresponding document page if record is located on another page.
     */
    onSelectRecord(rec) {
        this.state.selectedTempId = rec.temp_id;
        if (rec.page_number && rec.page_number !== this.state.currentPage) {
            this.state.currentPage = rec.page_number;
        }
    }

    /**
     * Manually add a new transaction line
     */
    onAddManualRecord() {
        const newId = `rec_manual_${Date.now()}`;
        const newRecord = {
            temp_id: newId,
            date: new Date().toISOString().slice(0, 10),
            partner_name: "",
            account_number: "",
            ref: "",
            narration: "",
            amount: 0.0,
            page_number: this.state.currentPage,
            is_manual: true,
        };
        this.state.records.unshift(newRecord);
        this.state.selectedTempId = newId;

        this.notification.add(_t("Added a new manual transaction line."), {
            type: "info",
            sticky: false,
        });
    }

    onDeleteRecord(tempId) {
        const idx = this.state.records.findIndex((r) => r.temp_id === tempId);
        if (idx !== -1) {
            this.state.records.splice(idx, 1);
            if (this.state.selectedTempId === tempId) {
                this.state.selectedTempId = this.state.records.length > 0 ? this.state.records[0].temp_id : null;
            }
        }
    }

    onPrevPage() {
        if (this.state.currentPage > 1) {
            this.state.currentPage -= 1;
        }
    }

    onNextPage() {
        if (this.state.currentPage < this.state.totalPages) {
            this.state.currentPage += 1;
        }
    }

    onZoomIn() {
        this.state.zoom = Math.min(250, this.state.zoom + 15);
    }

    onZoomOut() {
        this.state.zoom = Math.max(50, this.state.zoom - 15);
    }

    onZoomReset() {
        this.state.zoom = 100;
    }

    async onClose() {
        await this.actionService.doAction({ type: "ir.actions.act_window_close" });
    }

    /**
     * Confirm and import transaction lines into bank journal
     */
    async onConfirmImport() {
        if (!this.state.records || this.state.records.length === 0) {
            this.notification.add(_t("No transaction lines to import."), {
                type: "warning",
            });
            return;
        }

        this.state.isSaving = true;
        try {
            const resultAction = await this.orm.call(
                "account.statement.preview",
                "action_confirm",
                [[this.previewId]],
                {
                    updated_records: this.state.records,
                }
            );

            this.notification.add(
                _t("Successfully saved and imported %s transaction lines into journal %s!").replace("%s", this.state.records.length).replace("%s", this.journalName),
                { type: "success", sticky: false }
            );

            if (resultAction && typeof resultAction === "object") {
                await this.actionService.doAction(resultAction, { clearBreadcrumbs: true });
            } else {
                await this.actionService.doAction({ type: "ir.actions.act_window_close" });
            }
        } catch (error) {
            console.error("Error confirming statement lines:", error);
            this.notification.add(_t("Error importing transactions: ") + (error.message || error), {
                type: "danger",
            });
        } finally {
            this.state.isSaving = false;
        }
    }
}

registry.category("actions").add("statement_preview", StatementPreviewDialog);
