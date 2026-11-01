# -*- coding: utf-8 -*-
import base64
import logging
from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


class StatementPreviewController(http.Controller):

    @http.route(
        "/account_reconcile_mcp_support/page_image/<int:attachment_id>/<int:page_number>",
        type="http",
        auth="user",
        methods=["GET"],
        csrf=False,
    )
    def get_statement_page_image(self, attachment_id, page_number=1, **kwargs):
        """
        Renders a specific page of an uploaded PDF attachment as a high-resolution PNG image on demand.
        If the attachment is already an image (PNG, JPG, WebP), returns the image directly.
        """
        attachment = request.env["ir.attachment"].browse(attachment_id)
        if not attachment.exists() or not attachment.datas:
            return request.not_found()

        mimetype = attachment.mimetype or ""
        filename = (attachment.name or "").lower()
        file_bytes = base64.b64decode(attachment.datas)

        # If it's a direct image file
        if mimetype.startswith("image/") or filename.endswith((".png", ".jpg", ".jpeg", ".webp")):
            out_mime = mimetype if mimetype.startswith("image/") else "image/jpeg"
            return request.make_response(file_bytes, headers=[
                ("Content-Type", out_mime),
                ("Cache-Control", "public, max-age=3600"),
            ])

        # If it's a PDF -> render requested page to PNG with fitz (PyMuPDF)
        try:
            import fitz
            doc = fitz.open(stream=file_bytes, filetype="pdf")
            total_pages = len(doc)
            if total_pages == 0:
                return request.not_found()
            page_idx = max(0, min(page_number - 1, total_pages - 1))
            page = doc[page_idx]
            pix = page.get_pixmap(dpi=150)
            png_bytes = pix.tobytes("png")
            return request.make_response(png_bytes, headers=[
                ("Content-Type", "image/png"),
                ("Cache-Control", "public, max-age=3600"),
            ])
        except Exception as e:
            _logger.exception("Error rendering PDF page %d for attachment %d: %s", page_number, attachment_id, e)
            return request.not_found()
