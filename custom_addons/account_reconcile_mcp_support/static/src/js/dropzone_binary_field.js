/** @odoo-module **/

import { Component, useState, useRef } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { standardFieldProps } from "@web/views/fields/standard_field_props";
import { _t } from "@web/core/l10n/translation";

export class DropzoneBinaryField extends Component {
    static template = "account_reconcile_mcp_support.DropzoneBinaryField";
    static props = {
        ...standardFieldProps,
    };

    setup() {
        this.fileInputRef = useRef("fileInput");
        this.state = useState({
            isDragging: false,
            isUploading: false,
            fileName: this.props.record.data.file_name || "",
            fileSizeStr: "",
            fileType: "",
        });
    }

    get hasFile() {
        return Boolean(
            this.props.record.data[this.props.name] ||
            (this.props.record.data.attachment_id && this.props.record.data.attachment_id[0])
        );
    }

    get fileName() {
        return this.props.record.data.file_name || this.state.fileName || _t("Selected document");
    }

    onDragOver(ev) {
        ev.preventDefault();
        ev.stopPropagation();
        this.state.isDragging = true;
    }

    onDragLeave(ev) {
        ev.preventDefault();
        ev.stopPropagation();
        this.state.isDragging = false;
    }

    onDrop(ev) {
        ev.preventDefault();
        ev.stopPropagation();
        this.state.isDragging = false;

        const files = ev.dataTransfer ? ev.dataTransfer.files : null;
        if (files && files.length > 0) {
            this.processFile(files[0]);
        }
    }

    onClickUpload() {
        if (this.fileInputRef.el) {
            this.fileInputRef.el.click();
        }
    }

    onFileChange(ev) {
        const files = ev.target.files;
        if (files && files.length > 0) {
            this.processFile(files[0]);
        }
    }

    async processFile(file) {
        this.state.isUploading = true;
        this.state.fileName = file.name;
        this.state.fileSizeStr = this.formatBytes(file.size);
        this.state.fileType = file.type || file.name.split('.').pop();

        try {
            const formData = new FormData();
            formData.append("ufile", file);
            formData.append("csrf_token", odoo.csrf_token);
            formData.append("model", this.props.record.resModel || "account.statement.upload.wizard");
            formData.append("id", this.props.record.resId || "0");

            const response = await fetch("/web/binary/upload_attachment", {
                method: "POST",
                body: formData,
            });

            const result = await response.json();
            const updates = {};
            if (Array.isArray(result) && result.length > 0 && result[0].id) {
                const attachment = result[0];
                if (this.props.record.fields.attachment_id) {
                    updates.attachment_id = [attachment.id, attachment.filename || file.name];
                }
            }
            if (this.props.record.fields.file_name) {
                updates.file_name = file.name;
            }

            // Read base64 into binary field
            const reader = new FileReader();
            reader.onload = (e) => {
                const dataUrl = e.target.result;
                updates[this.props.name] = dataUrl.split(",")[1] || dataUrl;
                this.props.record.update(updates);
            };
            reader.readAsDataURL(file);
        } catch (err) {
            console.error("File upload failed:", err);
        } finally {
            this.state.isUploading = false;
        }
    }

    onRemoveFile(ev) {
        ev.stopPropagation();
        const updates = {
            [this.props.name]: false,
        };
        if (this.props.record.fields.attachment_id) {
            updates.attachment_id = false;
        }
        if (this.props.record.fields.file_name) {
            updates.file_name = "";
        }
        this.state.fileName = "";
        this.state.fileSizeStr = "";
        this.state.fileType = "";
        this.props.record.update(updates);
        if (this.fileInputRef.el) {
            this.fileInputRef.el.value = "";
        }
    }

    formatBytes(bytes, decimals = 2) {
        if (bytes === 0) return '0 Bytes';
        const k = 1024;
        const dm = decimals < 0 ? 0 : decimals;
        const sizes = ['Bytes', 'KB', 'MB', 'GB'];
        const i = Math.floor(Math.log(bytes) / Math.log(k));
        return parseFloat((bytes / Math.pow(k, i)).toFixed(dm)) + ' ' + sizes[i];
    }
}

export const dropzoneBinaryField = {
    component: DropzoneBinaryField,
    supportedTypes: ["binary"],
};

if (!registry.category("fields").contains("account_reconcile_dropzone_binary")) {
    registry.category("fields").add("account_reconcile_dropzone_binary", dropzoneBinaryField);
}
