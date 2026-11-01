{
    "name": "Account Reconcile 3-Way Matching",
    "version": "18.0.1.0.0",
    "category": "Accounting",
    "summary": "Purchase and sales three-way matching",
    "license": "LGPL-3",
    "depends": [
        "account_reconcile_mcp_support",
        "purchase_stock",
        "sale_stock",
    ],
    "data": [
        "security/sale_reconcile_security.xml",
        "security/purchase_reconcile_security.xml",
        "security/ir.model.access.csv",
        "views/sale_match_views.xml",
        "views/purchase_match_views.xml",
        "views/res_config_settings_views.xml",
    ],
    "installable": True,
}
