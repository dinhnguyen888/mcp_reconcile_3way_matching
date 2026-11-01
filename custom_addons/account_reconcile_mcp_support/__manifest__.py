# -*- coding: utf-8 -*-
{
    "name": "Account Reconcile MCP Support",
    "version": "18.0.2.1.0",
    "category": "Accounting",
    "summary": "MCP import for vendor bills, receipts, and bank statements",
    "author": "DinhERP",
    "license": "LGPL-3",
    "depends": ["account_reconcile_oca", "account_reconcile_3way_matching"],
    "data": [
        "security/import_security.xml",
        "security/ir.model.access.csv",
        "views/statement_upload_wizard_views.xml",
        "views/statement_preview_views.xml",
        "views/account_move_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "account_reconcile_mcp_support/static/src/scss/dropzone_binary.scss",
            "account_reconcile_mcp_support/static/src/scss/statement_preview_dialog.scss",
            "account_reconcile_mcp_support/static/src/xml/dropzone_binary_field.xml",
            "account_reconcile_mcp_support/static/src/xml/reconcile_buttons.xml",
            "account_reconcile_mcp_support/static/src/xml/statement_preview_dialog.xml",
            "account_reconcile_mcp_support/static/src/js/dropzone_binary_field.js",
            "account_reconcile_mcp_support/static/src/js/mcp_document_client.js",
            "account_reconcile_mcp_support/static/src/js/statement_upload_wizard.js",
            "account_reconcile_mcp_support/static/src/js/statement_preview_dialog.js",
        ],
    },
    "installable": True,
    "application": False,
    "auto_install": False,
}
