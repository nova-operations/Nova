import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from nova.telegram_bot import is_authorized, handle_message, handle_multimodal, start


@pytest.fixture
def mock_update():
    update = MagicMock()
    update.effective_user.id = 123456
    update.effective_chat.id = 456
    update.message.text = "Hello Nova"
    update.message.caption = None
    update.message.voice = None
    update.message.audio = None
    update.message.photo = None
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
    mock_update.message.voice = MagicMock(file_id="voice123")

    # Mock bot.get_file and its download method
    mock_file = AsyncMock()
    mock_file.download_as_bytearray = AsyncMock(
        return_value=bytearray(b"fake audio data")
    )
    mock_context.bot.get_file = AsyncMock(return_value=mock_file)

    with patch("nova.telegram_bot.is_authorized", return_value=True):
        with patch(
            "nova.telegram_bot.handle_message", new_callable=AsyncMock
        ) as mock_hm:
            await handle_multimodal(mock_update, mock_context)
            assert mock_hm.called
            assert "User sent a voice message" in mock_hm.call_args[1]["override_text"]
            assert "audio" in mock_hm.call_args[1]
            assert len(mock_hm.call_args[1]["audio"]) == 1


@pytest.mark.asyncio
async def test_handle_multimodal_photo(mock_update, mock_context):
    mock_update.message.photo = [MagicMock(file_id="photo123")]

    # Mock bot.get_file and its download method
    mock_file = AsyncMock()
    mock_file.download_as_bytearray = AsyncMock(
        return_value=bytearray(b"fake photo data")
    )
    mock_context.bot.get_file = AsyncMock(return_value=mock_file)

    with patch("nova.telegram_bot.is_authorized", return_value=True):
        with patch(
            "nova.telegram_bot.handle_message", new_callable=AsyncMock
        ) as mock_hm:
            await handle_multimodal(mock_update, mock_context)
            assert mock_hm.called
            assert "User sent a photo" in mock_hm.call_args[1]["override_text"]
            assert "images" in mock_hm.call_args[1]
            assert len(mock_hm.call_args[1]["images"]) == 1
