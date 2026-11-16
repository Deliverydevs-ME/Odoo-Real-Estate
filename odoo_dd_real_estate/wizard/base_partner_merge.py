from odoo import models


class BasePartnerMergeAutomaticWizard(models.TransientModel):
    _inherit = 'base.partner.merge.automatic.wizard'

    def _merge(self, partner_ids, dst_partner=None, extra_checks=True):
        # While merging, the source and destination partners briefly share the
        # same Trade License / ORN / BRN, which the duplicate check would reject.
        self = self.with_context(re_skip_duplicate_check=True)
        return super()._merge(partner_ids, dst_partner=dst_partner, extra_checks=extra_checks)
