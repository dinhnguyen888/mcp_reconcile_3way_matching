{
    "name": "Purchase Three-Way Matching",
    "version": "18.0.2.0.0",
    "category": "Accounting",
    "summary": "Match purchase orders, receipts, and vendor bills",
    "license": "LGPL-3",
    "depends": [
        "account",
        "purchase_stock",
    ],
    "data": [
        "security/purchase_reconcile_security.xml",
        "security/ir.model.access.csv",
        "views/purchase_match_views.xml",
        "views/res_config_settings_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "account_reconcile_3way_matching/static/src/scss/purchase_match_workspace.scss",
            "account_reconcile_3way_matching/static/src/xml/purchase_match_workspace.xml",
            "account_reconcile_3way_matching/static/src/js/purchase_match_workspace.js",
        ],
    },
    "installable": True,
}
