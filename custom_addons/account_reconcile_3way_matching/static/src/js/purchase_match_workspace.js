/** @odoo-module **/

import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";

export class PurchaseMatchWorkspace extends Component {
    static template = "account_reconcile_3way_matching.PurchaseMatchWorkspace";
    static props = { action: Object, "*": true };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.matchId = this.props.action.params.match_id;
        this.state = useState({ data: null, loading: true });
        onWillStart(() => this.load());
    }

    async load() {
        this.state.loading = true;
        this.state.data = await this.orm.call(
            "reconcile.purchase.match",
            "get_workspace_data",
            [[this.matchId]]
        );
        this.state.loading = false;
    }

    formatMoney(value) {
        return new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 }).format(value || 0);
    }

    async confirm() {
        await this.orm.call("reconcile.purchase.match", "action_confirm", [[this.matchId]]);
        this.notification.add(_t("Three-way comparison refreshed."), { type: "success" });
        await this.load();
    }

    async approve() {
        await this.orm.call("reconcile.purchase.match", "action_approve", [[this.matchId]]);
        this.notification.add(_t("Exception approved."), { type: "success" });
        await this.load();
    }

    openModel(model, id) {
        return this.action.doAction({
            type: "ir.actions.act_window",
            res_model: model,
            res_id: id,
            views: [[false, "form"]],
            target: "current",
        });
    }
}

registry.category("actions").add("purchase_3way_workspace", PurchaseMatchWorkspace);
