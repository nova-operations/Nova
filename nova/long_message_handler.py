"""
Long Message Protocol Handler for Telegram

This module handles messages that exceed Telegram's 4096 character limit
by converting them to PDF documents when necessary.

IMPORTANT: This module operates in PLAINTEXT-ONLY mode.
- All Markdown is automatically stripped before sending
- parse_mode is ALWAYS None (no HTML formatting)
- HTML tags are aggressively removed
"""

import os
import re
import logging
import tempfile
from typing import Optional
from fpdf import FPDF
from html import escape

# Configure logging
logger = logging.getLogger(__name__)

# Telegram message length limit (with some buffer for safety)
TELEGRAM_MAX_LENGTH = 4000

# Configuration: Force plaintext mode for Telegram
# When True, all markdown characters are stripped from messages
FORCE_PLAINTEXT = os.getenv("FORCE_PLAINTEXT", "true").lower() == "true"


def strip_all_formatting(text: str) -> str:
    """
    Strip ALL formatting (HTML and Markdown) from text for Telegram compatibility.

    Removes:
    - HTML tags (<b>, <i>, <code>, <pre>, <a>, etc.)
    - Markdown headers (# ## ###)
    - Bold (**text** or __text__)
    - Italic (*text* or _text_)
    - Code blocks (```code```)
    - Inline code (`code`)
    - Links [text](url)
    - Bullet lists (- * +)
    - Numbered lists (1. 2. 3.)
    - Blockquotes (> text)

    Args:
        text: The text with potential formatting

    Returns:
        Clean plaintext with no formatting characters
    """
    if not text:
        return text

    result = text

    # Remove HTML tags (<...>) - aggressive, handles <b>, <i>, <code>, etc.
    result = re.sub(r"<[^>]+>", "", result)

    # Remove code blocks (```...```)
    result = re.sub(r"```[\s\S]*?```", "", result)

    # Remove inline code (`...`)
    result = re.sub(r"`([^`]+)`", r"\1", result)

    # Remove headers (# ## ###)
    result = re.sub(r"^#{1,6}\s+", "", result, flags=re.MULTILINE)

    # Remove bold (**text** or __text__)
    result = re.sub(r"\*\*([^*]+)\*\*", r"\1", result)
    result = re.sub(r"__([^_]+)__", r"\1", result)

    # Remove italic (*text* or _text_)
    result = re.sub(r"(?<!\*)\*(?!\*)([^*]+)(?<!\*)\*(?!\*)", r"\1", result)
    result = re.sub(r"(?<!_)_(?!_)([^_]+)(?<!_)_(?!_)", r"\1", result)

    # Remove links [text](url) - keep text only
    result = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", result)

    # Remove bullet list markers at start of lines
    result = re.sub(r"^[\-\*\+]\s+", "", result, flags=re.MULTILINE)

    # Remove numbered lists at start of lines
    result = re.sub(r"^\d+\.\s+", "", result, flags=re.MULTILINE)

    # Remove blockquotes
    result = re.sub(r"^>\s+", "", result, flags=re.MULTILINE)

    # Remove horizontal rules
    result = re.sub(r"^[\-\*_]{3,}\s*$", "", result, flags=re.MULTILINE)

    # Clean up excessive whitespace
    result = re.sub(r"\n{3,}", "\n\n", result)
    result = result.strip()

    return result


def strip_markdown(text: str) -> str:
    """
    Strip all Markdown formatting from text for Telegram compatibility.

    This is an alias for strip_all_formatting for backward compatibility.
    """
    return strip_all_formatting(text)


def sanitize_for_telegram(text: str, force_plaintext: bool = True) -> str:
    """
    Sanitize text for Telegram by removing ALL formatting (HTML and Markdown).

    Args:
        text: The text to sanitize
        force_plaintext: If True, strip all formatting characters (default: True)

    Returns:
        Sanitized text safe for Telegram
    """
    if force_plaintext or FORCE_PLAINTEXT:
        return strip_all_formatting(text)
    return text


def markdown_to_pdf_content(markdown_text: str) -> str:
    """
    Convert markdown-style text to clean PDF content.
    Handles basic formatting for PDF output.
    """
    # Clean up the text for PDF
    lines = markdown_text.split("\n")
    pdf_lines = []

    for line in lines:
        stripped = line.strip()

        # Handle markdown headers
        if line.startswith("### "):
            pdf_lines.append(f"\n{line[4:].upper()}\n")
        elif line.startswith("## "):
            pdf_lines.append(f"\n{line[3:].upper()}\n")
        elif line.startswith("# "):
            pdf_lines.append(f"\n{line[2:].upper()}\n")
        # Handle bullet points - use ASCII asterisk instead of unicode bullet
        elif stripped.startswith(("- ", "* ", "+ ")):
            pdf_lines.append(f"  * {stripped[2:]}")
        # Handle numbered lists (e.g., "1. Item")
        elif len(stripped) >= 2 and stripped[0].isdigit() and stripped[1] == ".":
            pdf_lines.append(f"  {stripped}")
        # Handle code blocks
        elif stripped.startswith("```"):
            pdf_lines.append("")
        # Regular text
        else:
            pdf_lines.append(line)

    return "\n".join(pdf_lines)


def create_pdf_from_text(text: str, title: str = "Nova Report") -> Optional[str]:
    """
    Create a PDF file from text content.

    Args:
        text: The text content to convert to PDF
        title: The title for the PDF document

    Returns:
        Path to the created PDF file, or None if creation failed
    """
    try:
        pdf = FPDF()
        pdf.add_page()

        # Set font
        pdf.set_font("Arial", size=11)

        # Add title - encode to handle any unicode
        safe_title = title.encode("latin-1", "replace").decode("latin-1")
        pdf.set_font("Arial", "B", 16)
        pdf.cell(200, 10, txt=safe_title, ln=True, align="C")
        pdf.ln(5)

        # Add separator line
        pdf.set_draw_color(100, 100, 100)
        pdf.line(10, pdf.get_y(), 200, pdf.get_y())
        pdf.ln(5)

        # Set font for content
        pdf.set_font("Arial", size=10)

        # Process and add content
        content = markdown_to_pdf_content(text)

        # Add each line
        for line in content.split("\n"):
            # Handle empty lines
            if not line.strip():
                pdf.ln(3)
                continue

            # Check if line is a header (all caps or marked as section)
            if line.isupper() and len(line) < 50:
                pdf.set_font("Arial", "B", 12)
                pdf.ln(5)
                # Encode to handle any remaining unicode
                header_text = line.encode("latin-1", "replace").decode("latin-1")
                pdf.cell(0, 10, txt=header_text, ln=True)
                pdf.set_font("Arial", size=10)
            else:
                # Clean the line for PDF (remove HTML entities, etc.)
                clean_line = line.replace("**", "").replace("*", "").replace("`", "")
                # Encode to handle unicode characters
                clean_line = clean_line.encode("latin-1", "replace").decode("latin-1")

                # Use multi_cell to handle long lines
                pdf.multi_cell(0, 5, txt=clean_line)

        # Add footer with timestamp
        pdf.ln(10)
        pdf.set_font("Arial", "I", 8)
        pdf.cell(0, 5, txt="Generated by Nova PM Framework", ln=True, align="C")

        # Save to temp file
        temp_dir = tempfile.gettempdir()
        pdf_filename = f"nova_report_{os.urandom(4).hex()}.pdf"
        pdf_path = os.path.join(temp_dir, pdf_filename)

        pdf.output(pdf_path)

        logger.info(f"PDF created successfully: {pdf_path}")
        return pdf_path

    except Exception as e:
        logger.error(f"Failed to create PDF: {e}")
        return None


def is_message_too_long(message: str) -> bool:
    """
    Check if a message exceeds the Telegram character limit.

    Args:
        message: The message to check

    Returns:
        True if message is too long, False otherwise
    """
    return len(message) > TELEGRAM_MAX_LENGTH


def process_long_message(
    message: str, title: str = "Nova Report"
) -> tuple[str, Optional[str], str]:
    """
    Process a message that might be too long for Telegram.

    Args:
        message: The message content
        title: Title for the PDF if conversion is needed

    Returns:
        Tuple of (summary_message, pdf_path, status)
        - summary_message: Short message to send via Telegram
        - pdf_path: Path to PDF file if created, None otherwise
        - status: 'sent_as_text', 'sent_as_pdf', or 'error'
    """
    # CRITICAL: Always sanitize ALL formatting before processing
    message = strip_all_formatting(message)

    if not is_message_too_long(message):
        # Message fits within limits
        return message, None, "sent_as_text"

    logger.info(
        f"Message length ({len(message)}) exceeds Telegram limit. Converting to PDF..."
    )

    # Create PDF from the full message
    pdf_path = create_pdf_from_text(message, title)

    if pdf_path is None:
        # PDF creation failed, try to send truncated message
        truncated = (
            message[: TELEGRAM_MAX_LENGTH - 100]
            + "\n\n[Message truncated - PDF generation failed]"
        )
        return truncated, None, "error"

    # Create a summary message (plaintext only)
    summary = (
        f"Report Generated\n\n"
        f"The report is too long for Telegram ({len(message)} chars > {TELEGRAM_MAX_LENGTH} limit).\n"
        f"I've converted it to a PDF document which is attached to this message.\n\n"
        f"Note: If the PDF doesn't appear, you can request it again."
    )

    return summary, pdf_path, "sent_as_pdf"


async def send_message_with_fallback(
    bot,
    chat_id: int,
    message: str,
    title: str = "Nova Report",
    parse_mode: Optional[str] = None,  # Default to None - plaintext only
) -> tuple[bool, str]:
    """
    Send a message to Telegram, automatically converting to PDF if too long.

    IMPORTANT: This function ALWAYS operates in PLAINTEXT-ONLY mode.
    - All HTML and Markdown are stripped before sending
    - parse_mode is ALWAYS None

    Args:
        bot: The telegram bot instance
        chat_id: The target chat ID
        message: The message content
        title: Title for the PDF if conversion is needed
        parse_mode: Ignored - always uses None for plaintext

    Returns:
        Tuple of (success: bool, status: str)
    """
    # CRITICAL FIX: Always strip ALL formatting before sending
    # This removes HTML tags (<b>, <i>, etc.) AND Markdown
    message = strip_all_formatting(message)

    # Force parse_mode to None for plaintext (HTML is NEVER used)
    parse_mode = None

    if is_message_too_long(message):
        summary, pdf_path, status = process_long_message(message, title)

        if pdf_path and os.path.exists(pdf_path):
            try:
                # Send the PDF (caption will also be sanitized)
                with open(pdf_path, "rb") as pdf_file:
                    await bot.send_document(
                        chat_id=chat_id,
                        document=pdf_file,
                        caption=summary,
                        parse_mode=parse_mode,  # None = plaintext
                    )

                # Clean up temp PDF file
                try:
                    os.remove(pdf_path)
                except Exception as e:
                    logger.warning(f"Failed to remove temp PDF: {e}")

                logger.info(f"Long message sent as PDF to chat {chat_id}")
                return True, "sent_as_pdf"

            except Exception as e:
                logger.error(f"Failed to send PDF: {e}")
                # Fall back to truncated message
                status = "error"

    # Default: send as regular text message (plaintext only)
    try:
        await bot.send_message(
            chat_id=chat_id, text=message, parse_mode=parse_mode  # None = plaintext
        )
        return True, "sent_as_text"
    except Exception as e:
        logger.error(f"Failed to send message: {e}")
        return False, "error"
