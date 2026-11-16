from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.fields import Command


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    re_project_id = fields.Many2one(
        're.project', string="Real Estate Project", index=True,
        compute='_compute_re_project_id', store=True, readonly=False)
    re_unit_ids = fields.Many2many('re.unit', compute='_compute_re_unit_ids', string="Units")
    re_unit_id = fields.Many2one(
        're.unit', string="Unit", copy=False, index=True,
        compute='_compute_re_unit_id', store=True,
        help="Main unit of the order (first unit selected on the order lines).")
    re_agency_id = fields.Many2one('res.partner', string="Agency",
                                      domain=[('re_partner_type', '=', 'agency')])
    re_broker_id = fields.Many2one('res.partner', string="Broker",
                                      domain="[('re_partner_type', '=', 'broker')]")
    re_payment_plan_id = fields.Many2one(
        're.payment.plan', string="Payment Plan",
        compute='_compute_re_payment_plan_id', store=True, readonly=False,
        help="Template used to build the schedule. The generated schedule can then be edited "
             "freely for this customer (amounts, dates, number of installments).")
    re_schedule_start_date = fields.Date(
        string="Schedule Start Date", default=fields.Date.context_today,
        help="Booking date: due date of the first payment and reference for the following ones.")
    re_installment_ids = fields.One2many('re.installment', 'order_id', string="Payment Schedule",
                                            copy=False)
    re_installment_count = fields.Integer(compute='_compute_re_schedule')
    re_schedule_total = fields.Monetary(compute='_compute_re_schedule', string="Scheduled Total")
    re_schedule_difference = fields.Monetary(compute='_compute_re_schedule', string="Difference")
    re_amount_paid = fields.Monetary(compute='_compute_re_schedule', string="Paid")
    re_amount_outstanding = fields.Monetary(compute='_compute_re_schedule', string="Outstanding")
    re_next_due_date = fields.Date(compute='_compute_re_schedule', string="Next Due Date")
    re_overdue_amount = fields.Monetary(compute='_compute_re_schedule', string="Overdue")
    re_commission_ids = fields.One2many('re.commission', 'order_id', string="Commissions", copy=False)
    re_commission_count = fields.Integer(compute='_compute_re_commission_count')

    def _compute_re_commission_count(self):
        for order in self:
            order.re_commission_count = len(order.re_commission_ids.filtered(lambda c: not c.cancelled))

    def action_re_view_commissions(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _("Commissions"),
            'res_model': 're.commission',
            'view_mode': 'list,form',
            'domain': [('order_id', '=', self.id)],
        }

    @api.depends('amount_total', 're_installment_ids.amount', 're_installment_ids.state',
                 're_installment_ids.due_date', 're_installment_ids.invoice_id.amount_residual')
    def _compute_re_schedule(self):
        for order in self:
            insts = order.re_installment_ids.filtered(lambda i: i.state != 'cancelled')
            order.re_installment_count = len(insts)
            order.re_schedule_total = sum(insts.mapped('amount'))
            order.re_schedule_difference = order.amount_total - order.re_schedule_total if insts else 0.0
            order.re_amount_paid = sum(insts.mapped('amount_paid'))
            order.re_amount_outstanding = sum(insts.mapped('amount_residual'))
            pending = insts.filtered(lambda i: i.state in ('scheduled', 'invoiced', 'partial'))
            order.re_next_due_date = min(pending.mapped('due_date')) if pending else False
            order.re_overdue_amount = sum(pending.filtered('is_overdue').mapped('amount_residual'))

    @api.depends('order_line.re_unit_id')
    def _compute_re_unit_ids(self):
        for order in self:
            order.re_unit_ids = order.order_line.re_unit_id

    @api.depends('order_line.re_unit_id', 'order_line.sequence')
    def _compute_re_unit_id(self):
        for order in self:
            order.re_unit_id = order.order_line.sorted('sequence').re_unit_id[:1]

    @api.depends('re_unit_id')
    def _compute_re_project_id(self):
        for order in self:
            if order.re_unit_id:
                order.re_project_id = order.re_unit_id.project_id

    @api.depends('re_project_id')
    def _compute_re_payment_plan_id(self):
        for order in self:
            if not order.re_payment_plan_id and order.re_project_id.default_payment_plan_id:
                order.re_payment_plan_id = order.re_project_id.default_payment_plan_id

    # ------------------------------------------------------------------
    # Schedule
    # ------------------------------------------------------------------

    def action_re_generate_schedule(self):
        for order in self:
            if not order.re_payment_plan_id:
                raise UserError(_("Select a payment plan first."))
            invoiced = order.re_installment_ids.filtered(lambda i: i.invoice_id and i.state != 'cancelled')
            if invoiced:
                raise UserError(_(
                    "Some installments are already invoiced. Edit the remaining installments manually instead."))
            order.re_installment_ids.unlink()
            schedule = order.re_payment_plan_id._compute_schedule(
                order.amount_total,
                order.re_schedule_start_date or fields.Date.context_today(order),
                order.currency_id,
                handover_date=order.re_project_id.handover_date,
            )
            order.re_installment_ids = [Command.create(vals) for vals in schedule]
        return True

    def action_re_rebalance_schedule(self):
        """Spread the difference between order total and schedule over the not-yet-invoiced installments.

        Down payments are kept as agreed with the customer; the difference goes to the other
        open installments proportionally (or to all open installments if there is no other).
        """
        for order in self:
            not_invoiced = order.re_installment_ids.filtered(lambda i: not i.invoice_id)
            open_insts = not_invoiced.filtered(lambda i: i.milestone_type != 'down_payment') or not_invoiced
            if not open_insts or order.currency_id.is_zero(order.re_schedule_difference):
                continue
            remaining = order.amount_total - sum(
                (order.re_installment_ids - open_insts).filtered(lambda i: i.state != 'cancelled').mapped('amount'))
            open_total = sum(open_insts.mapped('amount'))
            allocated = 0.0
            for inst in open_insts[:-1]:
                share = inst.amount / open_total if open_total else 1 / len(open_insts)
                amount = order.currency_id.round(remaining * share)
                inst.amount = amount
                allocated += amount
            open_insts[-1].amount = order.currency_id.round(remaining - allocated)
            for inst in not_invoiced:
                inst.percentage = inst.amount / order.amount_total * 100 if order.amount_total else 0.0
        return True

    def _re_check_schedule(self):
        for order in self.filtered('re_installment_ids'):
            if not order.currency_id.is_zero(order.re_schedule_difference):
                raise UserError(_(
                    "The payment schedule of %(order)s totals %(scheduled)s but the order total is %(total)s. "
                    "Adjust the installments or click 'Rebalance'.",
                    order=order.name,
                    scheduled=order.currency_id.format(order.re_schedule_total),
                    total=order.currency_id.format(order.amount_total)))

    def action_confirm(self):
        for order in self.filtered(lambda o: o.re_payment_plan_id and not o.re_installment_ids):
            order.action_re_generate_schedule()
        self._re_check_schedule()
        for order in self:
            for unit in order.re_unit_ids:
                if unit.state not in ('available', 'reserved') \
                        or (unit.sale_order_id and unit.sale_order_id != order):
                    raise UserError(_("Unit %s is not available anymore.", unit.display_name))
        res = super().action_confirm()
        today = fields.Date.context_today(self)
        for order in self:
            if order.re_unit_ids:
                order.re_unit_ids.write({'state': 'booked', 'sale_order_id': order.id})
                order.message_post(body=_("Unit(s) booked: %s", ', '.join(order.re_unit_ids.mapped('display_name'))))
            # Down payment / anything already due is invoiced immediately
            due_now = order.re_installment_ids.filtered(lambda i: not i.invoice_id and i.due_date <= today)
            for inst in due_now.sorted('sequence'):
                inst._create_invoice()
        self.env['re.commission']._create_for_orders(self)
        return res

    def _action_cancel(self):
        res = super()._action_cancel()
        commissions = self.re_commission_ids.filtered(lambda c: not c.cancelled)
        commissions.filtered(lambda c: not c.billed_amount).write({'cancelled': True})
        for commission in commissions.filtered('billed_amount'):
            commission.message_post(body=_("Sales order cancelled: credit the commission bill manually."))
        units = self.env['re.unit'].search([('sale_order_id', 'in', self.ids)])
        units.write({'state': 'available', 'sale_order_id': False})
        return res

    def _re_down_payment_received(self):
        """Down payment = the 'Down Payment / Booking' installments (or the first one); without a
        payment schedule, the first posted customer invoice."""
        self.ensure_one()
        insts = self.re_installment_ids.filtered(lambda i: i.state != 'cancelled')
        if insts:
            down = insts.filtered(lambda i: i.milestone_type == 'down_payment') or insts.sorted('sequence')[:1]
            return all(i.state == 'paid' for i in down)
        invoices = self.invoice_ids.filtered(lambda m: m.move_type == 'out_invoice' and m.state == 'posted')
        return bool(invoices) and invoices.sorted('id')[0].payment_state in ('paid', 'in_payment')

    def _re_update_unit_sold(self):
        """Booked units become Sold as soon as the down payment is received."""
        for order in self.filtered(lambda o: o.state == 'sale' and o.re_unit_ids):
            booked = order.re_unit_ids.filtered(lambda u: u.state == 'booked')
            if booked and order._re_down_payment_received():
                booked.state = 'sold'
                order.message_post(body=_("Down payment received: unit(s) %s marked as sold.",
                                          ', '.join(booked.mapped('display_name'))))

    def action_re_view_installments(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _("Payment Schedule"),
            'res_model': 're.installment',
            'view_mode': 'list,form',
            'domain': [('order_id', '=', self.id)],
            'context': {'default_order_id': self.id},
        }
