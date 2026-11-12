from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError
from odoo.tools import float_compare, float_round


class RealEstatePaymentPlan(models.Model):
    _name = 're.payment.plan'
    _description = "Payment Plan"
    _order = 'sequence, name'

    name = fields.Char(required=True, translate=True)
    sequence = fields.Integer(default=10)
    line_ids = fields.One2many('re.payment.plan.line', 'plan_id', string="Milestones", copy=True)
    total_percentage = fields.Float(compute='_compute_total_percentage')
    installment_count = fields.Integer(compute='_compute_total_percentage', string="Number of Payments")
    note = fields.Text()
    active = fields.Boolean(default=True)

    @api.depends('line_ids.percentage', 'line_ids.installment_count')
    def _compute_total_percentage(self):
        for plan in self:
            plan.total_percentage = sum(plan.line_ids.mapped('percentage'))
            plan.installment_count = sum(plan.line_ids.mapped('installment_count'))

    @api.constrains('line_ids')
    def _check_total_percentage(self):
        for plan in self:
            if plan.line_ids and float_compare(plan.total_percentage, 100.0, precision_digits=4):
                raise ValidationError(_(
                    "The milestones of payment plan %(plan)s must total 100%% (currently %(total)s%%).",
                    plan=plan.name, total=round(plan.total_percentage, 4)))

    def _compute_schedule(self, total_amount, start_date, currency, handover_date=False):
        """Return a list of installment values for the given total.

        Each milestone line is spread over `installment_count` payments separated by
        `interval_months`. Lines follow each other: `start_after_months` is counted from
        the last payment of the previous line (or from `start_date` for the first line).
        The last payment absorbs any rounding difference so that the total is exact.
        """
        self.ensure_one()
        schedule = []
        cursor = start_date
        for line in self.line_ids.sorted('sequence'):
            count = max(line.installment_count, 1)
            if line.milestone_type == 'handover' and handover_date:
                first_due = handover_date
            else:
                first_due = cursor + relativedelta(months=line.start_after_months)
            share = line.percentage / count
            for i in range(count):
                due = first_due if line.milestone_type == 'handover' and handover_date \
                    else first_due + relativedelta(months=line.interval_months * i)
                label = line.name if count == 1 else _("%(name)s %(i)s/%(n)s", name=line.name, i=i + 1, n=count)
                schedule.append({
                    'name': label,
                    'milestone_type': line.milestone_type,
                    'due_date': due,
                    'percentage': share,
                    'amount': currency.round(total_amount * share / 100.0),
                })
                cursor = max(cursor, due)
        if schedule:
            diff = currency.round(total_amount - sum(s['amount'] for s in schedule))
            schedule[-1]['amount'] = currency.round(schedule[-1]['amount'] + diff)
        for seq, vals in enumerate(schedule, start=1):
            vals['sequence'] = seq
            vals['percentage'] = float_round(vals['percentage'], precision_digits=6)
        return schedule


class RealEstatePaymentPlanLine(models.Model):
    _name = 're.payment.plan.line'
    _description = "Payment Plan Milestone"
    _order = 'plan_id, sequence, id'

    plan_id = fields.Many2one('re.payment.plan', required=True, ondelete='cascade')
    sequence = fields.Integer(default=10)
    name = fields.Char(string="Label", required=True, translate=True)
    milestone_type = fields.Selection(
        [('down_payment', "Down Payment / Booking"), ('installment', "Installments"),
         ('construction', "Construction Milestone"), ('handover', "On Handover"),
         ('post_handover', "Post-Handover")],
        string="Type", required=True, default='installment')
    percentage = fields.Float(string="Total %", required=True, digits=(16, 4),
                              help="Share of the sale price covered by this milestone (all its payments).")
    installment_count = fields.Integer(string="No. of Payments", default=1, required=True)
    interval_months = fields.Integer(string="Every (months)", default=1, required=True)
    start_after_months = fields.Integer(
        string="Starts After (months)", default=0,
        help="Months between the previous milestone's last payment (or the booking date) and this "
             "milestone's first payment.")
    per_payment_percentage = fields.Float(compute='_compute_per_payment', string="% per Payment",
                                          digits=(16, 4))

    _positive = models.Constraint(
        'CHECK(percentage > 0 AND installment_count > 0 AND interval_months >= 0 AND start_after_months >= 0)',
        "Percentages and number of payments must be positive.")

    @api.depends('percentage', 'installment_count')
    def _compute_per_payment(self):
        for line in self:
            line.per_payment_percentage = line.percentage / line.installment_count if line.installment_count else 0.0
