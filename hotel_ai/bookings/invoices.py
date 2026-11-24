"""Invoice/receipt PDF generation for bookings."""

from __future__ import annotations

import io
import logging
from datetime import date as _date
from decimal import Decimal as _D
from typing import Optional

from django.conf import settings
from django.utils.translation import gettext_lazy as _

logger = logging.getLogger(__name__)


def generate_invoice_pdf(booking) -> bytes:
    """Return an invoice PDF as bytes for the given booking."""
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import cm
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
        from reportlab.lib import colors

        buf = io.BytesIO()
        doc = SimpleDocTemplate(
            buf,
            pagesize=A4,
            rightMargin=2 * cm,
            leftMargin=2 * cm,
            topMargin=2 * cm,
            bottomMargin=2 * cm,
        )

        styles = getSampleStyleSheet()
        title_style = ParagraphStyle(
            "InvoiceTitle",
            parent=styles["Heading1"],
            fontSize=22,
            spaceAfter=6,
            textColor=colors.HexColor("#1a1a2e"),
        )
        subtitle_style = ParagraphStyle(
            "Subtitle",
            parent=styles["Normal"],
            fontSize=10,
            textColor=colors.HexColor("#666666"),
            spaceAfter=12,
        )
        heading_style = ParagraphStyle(
            "SectionHeading",
            parent=styles["Heading2"],
            fontSize=12,
            spaceBefore=10,
            spaceAfter=6,
            textColor=colors.HexColor("#c9a84c"),
        )

        elements = []
        elements.append(Paragraph(_("Hotel Invoice"), title_style))
        elements.append(
            Paragraph(
                _("Grand Hotel AI — Generated: ") + _date.today().isoformat(),
                subtitle_style,
            )
        )
        elements.append(Spacer(1, 0.3 * cm))

        # Guest & booking info table
        info_data = [
            [_("Booking ID"), str(booking.id)],
            [_("Guest"), booking.client.full_name],
            [_("Email"), booking.client.email or "-"],
            [_("Room"), f"{booking.room.room_number} ({booking.room.room_type.name})"],
            [_("Check-in"), booking.check_in.isoformat()],
            [_("Check-out"), booking.check_out.isoformat()],
            [_("Nights"), str(booking.nights)],
            [_("Status"), _(booking.status)],
        ]
        if booking.rate_plan:
            info_data.extend([
                [_("Rate Plan"), booking.rate_plan.name],
                [_("Breakfast included"), _("Yes") if booking.rate_plan.includes_breakfast else _("No")],
            ])
        if booking.promo_codes.exists():
            codes = ", ".join(pc.code for pc in booking.promo_codes.all())
            info_data.append([_("Promo Codes"), codes])

        info_table = Table(info_data, colWidths=[3.5 * cm, 12 * cm])
        info_table.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f5f5f5")),
            ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#555555")),
            ("ALIGN", (0, 0), (0, -1), "RIGHT"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#dddddd")),
        ]))
        elements.append(info_table)
        elements.append(Spacer(1, 0.5 * cm))

        # Price breakdown
        price_data = [
            [_("Description"), _("Amount")],
        ]

        base_price = _calculate_base_price(booking)
        price_data.append([
            f"Room: {booking.room.room_number} — {booking.nights} night(s)",
            f"{base_price:.2f} {booking.currency}",
        ])

        if booking.rate_plan and booking.rate_plan.corporate_discount_pct:
            corp_disc = base_price * _D(str(booking.rate_plan.corporate_discount_pct)) / _D("100")
            price_data.append([
                f"Corporate discount ({booking.rate_plan.corporate_discount_pct}%)",
                f"-{corp_disc:.2f} {booking.currency}",
            ])

        total_tax_rate = getattr(settings, "HOTEL_TAX_RATE", 0)
        if total_tax_rate > 0:
            tax_amount = base_price * _D(str(total_tax_rate)) / _D("100")
            price_data.append([
                _("% Tax (%d%%)" % int(total_tax_rate)),
                f"{tax_amount:.2f} {booking.currency}",
            ])

        price_data.append([_("<b>Total</b>"), f"<b>{booking.total_price:.2f} {booking.currency}</b>"])

        price_table = Table(price_data, colWidths=[9 * cm, 5 * cm])
        price_table.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
            ("FONTSIZE", (0, 0), (-1, -2), 9),
            ("FONTSIZE", (0, -1), (-1, -1), 10),
            ("ALIGN", (0, 0), (0, -1), "LEFT"),
            ("ALIGN", (1, 0), (1, -1), "RIGHT"),
            ("TEXTCOLOR", (1, 0), (1, -2), colors.HexColor("#666666")),
            ("TEXTCOLOR", (1, -1), (1, -1), colors.HexColor("#27ae60")),
            ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#fff9e6")),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#dddddd")),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]))
        elements.append(Paragraph(_("Price Breakdown"), heading_style))
        elements.append(price_table)

        doc.build(elements)
        return buf.getvalue()
    except ImportError:
        logger.warning("reportlab not installed; returning None from generate_invoice_pdf")
        return b""


def _calculate_base_price(booking) -> _D:
    """Return the pre-discount base price for display on the invoice."""
    from hotel_ai.bookings.services import effective_price
    nights = booking.nights
    base = nights * effective_price(booking.room.room_type, booking.check_in)
    if booking.rate_plan and booking.rate_plan.stay_x_pay_nights is not None:
        pay_nights = min(booking.rate_plan.stay_x_pay_nights, nights)
        base = sum(
            effective_price(booking.room.room_type, booking.check_in + __import__("datetime").timedelta(days=i))
            for i in range(pay_nights)
        )
    return max(base, _D("0.00"))


__all__ = ["generate_invoice_pdf"]
