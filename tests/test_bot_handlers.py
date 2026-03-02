import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from nova.telegram_bot import is_authorized, handle_message, start


@pytest.fixture
def mock_update():
    update = MagicMock()
    update.effective_user.id = 123456
    update.effective_chat.id = 456
    update.message.text = "Hello Nova"
    update.message.message_id = 100
    update.message.caption = None
    update.message.voice = None
    update.message.audio = None
    update.message.photo = None
    update.message.video = None
    update.message.video_note = None
    update.message.document = None
    update.message.reply_to_message = None
    # No client-side quote by default
    update.message.quote = None
    return update


@pytest.fixture
def mock_context():
    context = MagicMock()
    context.bot = AsyncMock()
    return context


def test_is_authorized():
    with patch.dict("os.environ", {"TELEGRAM_USER_WHITELIST": "123,456"}):
        assert is_authorized(123) is True
        assert is_authorized(456) is True
        assert is_authorized(789) is False


@pytest.mark.asyncio
async def test_start_command(mock_update, mock_context):
    with patch("nova.telegram_bot.is_authorized", return_value=True):
        await start(mock_update, mock_context)
        mock_context.bot.send_message.assert_called_once()
        args, kwargs = mock_context.bot.send_message.call_args
        assert "Hello! I am Nova" in kwargs["text"]


@pytest.mark.asyncio
async def test_handle_message_auth_failed(mock_update, mock_context):
    with patch("nova.telegram_bot.is_authorized", return_value=False):
        await handle_message(mock_update, mock_context)
        mock_context.bot.send_chat_action.assert_not_called()


@pytest.mark.asyncio
async def test_handle_multimodal_voice(mock_update, mock_context):
    mock_update.message.text = None
    mock_update.message.voice = MagicMock(file_id="voice123")

    # Mock bot.get_file and its download method
    mock_file = AsyncMock()
    mock_file.download_as_bytearray = AsyncMock(
        return_value=bytearray(b"fake audio data")
    )
    mock_context.bot.get_file = AsyncMock(return_value=mock_file)

    with patch("nova.telegram_bot.is_authorized", return_value=True):
        with patch(
            "nova.telegram_bot.process_nova_intent", new_callable=AsyncMock
        ) as mock_pni:
            await handle_message(mock_update, mock_context)
            assert mock_pni.called
            assert "audio" in mock_pni.call_args[1]
            assert len(mock_pni.call_args[1]["audio"]) == 1
            assert mock_pni.call_args[1]["reply_to_message_id"] == 100


@pytest.mark.asyncio
async def test_handle_multimodal_photo(mock_update, mock_context):
    mock_update.message.text = None
    mock_update.message.photo = [MagicMock(file_id="photo123")]

    # Mock bot.get_file and its download method
    mock_file = AsyncMock()
    mock_file.download_as_bytearray = AsyncMock(
        return_value=bytearray(b"fake photo data")
    )
    mock_context.bot.get_file = AsyncMock(return_value=mock_file)

    with patch("nova.telegram_bot.is_authorized", return_value=True):
        with patch(
            "nova.telegram_bot.process_nova_intent", new_callable=AsyncMock
        ) as mock_pni:
            await handle_message(mock_update, mock_context)
            assert mock_pni.called
            assert "images" in mock_pni.call_args[1]
            assert len(mock_pni.call_args[1]["images"]) == 1
            assert mock_pni.call_args[1]["reply_to_message_id"] == 100


@pytest.mark.asyncio
async def test_reply_context_basic(mock_update):
    """Verify get_reply_context returns rich context for a replied text message."""
    from nova.telegram_bot import get_reply_context

    replied = MagicMock()
    replied.message_id = 42
    replied.from_user.first_name = "Morty"
    replied.from_user.is_bot = False
    replied.text = "original text"
    replied.caption = None
    replied.video_note = None
    replied.video = None
    replied.voice = None
    replied.audio = None
    replied.photo = None
    replied.sticker = None
    replied.document = None
    replied.animation = None
    replied.contact = None
    replied.location = None
    replied.poll = None

    mock_update.message.reply_to_message = replied

    ctx = await get_reply_context(mock_update)
    assert "Replied-to message_id: 42" in ctx
    assert "Author: Morty" in ctx
    assert "Text: original text" in ctx


@pytest.mark.asyncio
async def test_reply_context_video_note(mock_update):
    """Verify get_reply_context detects video_note (round video message)."""
    from nova.telegram_bot import get_reply_context

    replied = MagicMock()
    replied.message_id = 55
    replied.from_user.first_name = "Rick"
    replied.from_user.is_bot = False
    replied.text = None
    replied.caption = None
    replied.video_note = MagicMock()  # round video
    replied.video = None
    replied.voice = None
    replied.audio = None
    replied.photo = None
    replied.sticker = None
    replied.document = None
    replied.animation = None
    replied.contact = None
    replied.location = None
    replied.poll = None

    mock_update.message.reply_to_message = replied

    ctx = await get_reply_context(mock_update)
    assert "video_message" in ctx
    assert "round video note" in ctx


@pytest.mark.asyncio
async def test_msg_meta_injected(mock_update, mock_context):
    """Verify [MSG_META] header is prepended to the user message."""
    with patch("nova.telegram_bot.is_authorized", return_value=True):
        with patch(
            "nova.telegram_bot.process_nova_intent", new_callable=AsyncMock
        ) as mock_pni:
            await handle_message(mock_update, mock_context)
            sent_message = mock_pni.call_args[0][2]  # 3rd positional arg
            assert "[MSG_META" in sent_message
            assert "message_id=100" in sent_message


@pytest.mark.asyncio
async def test_chat_control_reply_to_message():
    """Test reply_to_message tool returns success."""
    from nova.tools.chat.chat_control import reply_to_message

    mock_bot = AsyncMock()
    sent_msg = MagicMock()
    sent_msg.message_id = 200
    mock_bot.send_message = AsyncMock(return_value=sent_msg)

    with patch("nova.tools.chat.chat_control._get_telegram_bot", return_value=mock_bot):
        result = await reply_to_message(chat_id="456", message_id=100, text="Hi")
        assert "Replied successfully" in result
        mock_bot.send_message.assert_called_once_with(
            chat_id=456,
            text="Hi",
            reply_to_message_id=100,
        )


@pytest.mark.asyncio
async def test_chat_control_pin_message():
    """Test pin_message tool returns success."""
    from nova.tools.chat.chat_control import pin_message

    mock_bot = AsyncMock()
    mock_bot.pin_chat_message = AsyncMock()

    with patch("nova.tools.chat.chat_control._get_telegram_bot", return_value=mock_bot):
        result = await pin_message(chat_id="456", message_id=100)
        assert "pinned successfully" in result
        mock_bot.pin_chat_message.assert_called_once()
