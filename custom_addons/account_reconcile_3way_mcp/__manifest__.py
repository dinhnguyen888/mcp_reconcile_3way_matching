{
    "name": "Purchase Three-Way Matching MCP Bridge",
    "version": "18.0.1.0.0",
    "category": "Accounting",
    "summary": "Scan vendor bills from the Accounting dashboard and open three-way matching",
    "license": "LGPL-3",
    "depends": [
        "account_reconcile_3way_matching",
        "account_reconcile_mcp_support",
    ],
    "data": [
        "views/account_journal_dashboard_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "account_reconcile_3way_mcp/static/src/js/account_file_uploader.js",
        ],
    },
    "installable": True,
}
