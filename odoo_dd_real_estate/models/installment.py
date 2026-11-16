from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class RealEstateInstallment(models.Model):
    _name = 're.installment'
    _description = "Payment Schedule Installment"
    _inherit = ['mail.thread']
    _order = 'order_id, sequence, due_date, id'

    order_id = fields.Many2one('sale.order', required=True, ondelete='cascade', index=True)
    partner_id = fields.Many2one(related='order_id.partner_id', store=True, string="Customer")
    project_id = fields.Many2one(related='order_id.re_project_id', store=True)
    unit_id = fields.Many2one(related='order_id.re_unit_id', store=True)
    company_id = fields.Many2one(related='order_id.company_id', store=True)
    currency_id = fields.Many2one(related='order_id.currency_id')
    sequence = fields.Integer(default=10)
    name = fields.Char(string="Description", required=True)
    milestone_type = fields.Selection(
        [('down_payment', "Down Payment / Booking"), ('installment', "Installment"),
         ('construction', "Construction Milestone"), ('handover', "On Handover"),
         ('post_handover', "Post-Handover")],
        string="Type", default='installment', required=True)
    due_date = fields.Date(required=True, index=True, tracking=True)
    percentage = fields.Float(string="%", digits=(16, 4))
    amount = fields.Monetary(required=True, tracking=True)
    invoice_id = fields.Many2one('account.move', string="Invoice", readonly=True, copy=False, index=True)
    invoice_state = fields.Selection(related='invoice_id.state', string="Invoice Status")
    amount_residual = fields.Monetary(compute='_compute_amounts', string="Amount Due")
    amount_paid = fields.Monetary(compute='_compute_amounts', string="Amount Paid")
    state = fields.Selection(
        [('scheduled', "Scheduled"), ('invoiced', "Invoiced"), ('partial', "Partially Paid"),
         ('paid', "Paid"), ('cancelled', "Cancelled")],
        compute='_compute_state', store=True, index=True, tracking=True)
    is_overdue = fields.Boolean(compute='_compute_is_overdue', search='_search_is_overdue')
    last_reminder_date = fields.Date(readonly=True, copy=False)
    reminder_count = fields.Integer(readonly=True, copy=False)

    @api.depends('invoice_id', 'invoice_id.state', 'invoice_id.payment_state')
    def _compute_state(self):
        for inst in self:
            invoice = inst.invoice_id
            if not invoice:
                inst.state = 'scheduled'
            elif invoice.state == 'cancel' or invoice.payment_state == 'reversed':
                inst.state = 'cancelled'
            elif invoice.payment_state in ('paid', 'in_payment'):
                inst.state = 'paid'
            elif invoice.payment_state == 'partial':
                inst.state = 'partial'
            else:
                inst.state = 'invoiced'

    @api.depends('amount', 'invoice_id.amount_residual', 'invoice_id.amount_total', 'invoice_id.state')
    def _compute_amounts(self):
        for inst in self:
            if inst.invoice_id and inst.invoice_id.state == 'posted':
                inst.amount_residual = inst.invoice_id.amount_residual
                inst.amount_paid = inst.invoice_id.amount_total - inst.invoice_id.amount_residual
            elif inst.state == 'cancelled':
                inst.amount_residual = inst.amount_paid = 0.0
            else:
                inst.amount_residual = inst.amount
                inst.amount_paid = 0.0

    @api.depends('due_date', 'state')
    def _compute_is_overdue(self):
        today = fields.Date.context_today(self)
        for inst in self:
            inst.is_overdue = inst.state in ('scheduled', 'invoiced', 'partial') and inst.due_date < today

    def _search_is_overdue(self, operator, value):
        if operator not in ('=', '!=') or not isinstance(value, bool):
            raise UserError(_("Unsupported search on overdue installments."))
        domain = [('state', 'in', ('scheduled', 'invoiced', 'partial')),
                  ('due_date', '<', fields.Date.context_today(self))]
        positive = (operator == '=') == value
        return domain if positive else ['!', '&'] + domain

    @api.onchange('amount')
    def _onchange_amount(self):
        total = self.order_id.amount_total
        self.percentage = self.amount / total * 100 if total else 0.0

    @api.onchange('percentage')
    def _onchange_percentage(self):
        amount = self.order_id.currency_id.round(self.order_id.amount_total * self.percentage / 100)
        if not self.order_id.currency_id.is_zero(amount - self.amount):
            self.amount = amount

    def write(self, vals):
        locked = {'amount', 'percentage'} & set(vals)
        if locked and self.filtered('invoice_id'):
            raise UserError(_("Invoiced installments cannot be changed. Cancel the invoice first."))
        return super().write(vals)

    def unlink(self):
        if self.filtered(lambda i: i.invoice_id and i.state != 'cancelled'):
            raise UserError(_("Invoiced installments cannot be deleted."))
        return super().unlink()

    # ------------------------------------------------------------------
    # Invoicing
    # ------------------------------------------------------------------

    def action_create_invoice(self):
        invoices = self.env['account.move']
        for inst in self.sorted(lambda i: (i.order_id.id, i.sequence)):
            invoices |= inst._create_invoice()
        if len(invoices) == 1:
            return {
                'type': 'ir.actions.act_window',
                'res_model': 'account.move',
                'res_id': invoices.id,
                'view_mode': 'form',
            }
        return True

    def _create_invoice(self):
        """Invoice the installment as a sale-order down payment of its exact amount."""
        self.ensure_one()
        if self.invoice_id and self.state != 'cancelled':
            return self.env['account.move']
        order = self.order_id
        if order.state != 'sale':
            raise UserError(_("Confirm the sales order %s before invoicing installments.", order.name))
        wizard = self.env['sale.advance.payment.inv'].with_company(order.company_id).with_context(
            active_model='sale.order', active_ids=order.ids, active_id=order.id,
        ).create({
            'advance_payment_method': 'fixed',
            'fixed_amount': self.amount,
            'sale_order_ids': [fields.Command.set(order.ids)],
        })
        invoice = wizard._create_invoices(order)
        label = _("%(order)s - %(name)s", order=order.name, name=self.name)
        if order.re_unit_id:
            label = _("%(label)s (Unit %(unit)s)", label=label, unit=order.re_unit_id.display_name)
        dp_lines = invoice.invoice_line_ids.filtered(lambda l: l.display_type == 'product')
        dp_lines.name = label
        if not order.locked:
            dp_lines.sale_line_ids.filtered('is_downpayment').name = label
        invoice.write({
            'invoice_date_due': self.due_date,
            'invoice_payment_term_id': False,
            'ref': label,
        })
        # A fixed due date must win over any payment term recomputation
        invoice.invoice_date_due = self.due_date
        self.invoice_id = invoice
        if order.company_id.re_auto_post_invoices:
            invoice.invoice_date = fields.Date.context_today(self)
            invoice.invoice_date_due = max(self.due_date, invoice.invoice_date)
            invoice.action_post()
        return invoice

    # ------------------------------------------------------------------
    # Reminders
    # ------------------------------------------------------------------

    def action_send_reminder(self):
        template = self.env.ref('odoo_dd_real_estate.mail_template_installment_reminder')
        for inst in self:
            if not inst.partner_id.email:
                inst.order_id.message_post(body=_(
                    "Installment reminder for %s not sent: the customer has no email address.", inst.name))
                continue
            template.send_mail(inst.id, force_send=False, email_layout_xmlid='mail.mail_notification_light')
            inst.order_id.message_post(body=_("Payment reminder sent for %(name)s (due %(date)s).",
                                              name=inst.name, date=inst.due_date))
            inst.write({'last_reminder_date': fields.Date.context_today(self),
                        'reminder_count': inst.reminder_count + 1})

    @api.model
    def _cron_process_installments(self):
        """Daily: invoice upcoming installments, send reminders, flag fully paid units."""
        today = fields.Date.context_today(self)
        for company in self.env['res.company'].search([]):
            base = [('company_id', '=', company.id), ('order_id.state', '=', 'sale')]

            to_invoice = self.search(base + [
                ('invoice_id', '=', False),
                ('due_date', '<=', today + timedelta(days=company.re_invoice_days_before)),
            ])
            for inst in to_invoice:
                try:
                    with self.env.cr.savepoint():
                        inst._create_invoice()
                except UserError as e:
                    inst.order_id.message_post(body=_("Automatic invoicing of %(name)s failed: %(error)s",
                                                      name=inst.name, error=e))

            open_insts = self.search(base + [('state', 'in', ('invoiced', 'partial'))])
            upcoming_limit = today + timedelta(days=company.re_reminder_days_before)
            interval = max(company.re_overdue_reminder_interval, 1)
            to_remind = open_insts.filtered(
                lambda i: (today <= i.due_date <= upcoming_limit and not i.last_reminder_date)
                or (i.due_date < today and (not i.last_reminder_date
                                            or i.last_reminder_date <= i.due_date
                                            or (today - i.last_reminder_date).days >= interval))
            )
            to_remind.action_send_reminder()

        self.env['re.unit'].search([('state', '=', 'booked')]).sale_order_id._re_update_unit_sold()
