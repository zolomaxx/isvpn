from urllib.parse import urlparse
import io

from aiogram import Router, F
from aiogram.exceptions import TelegramForbiddenError, TelegramNotFound, TelegramBadRequest
from aiogram.types import CallbackQuery, Message

from bot.database.models import Permission
from bot.database.methods import (
    check_category_cached, check_item_cached, create_item, add_values_to_item
)
from bot.keyboards.inline import back, question_buttons, simple_buttons
from bot.logger_mesh import audit_logger
from bot.filters import HasPermissionFilter
from bot.misc import EnvKeys
from bot.i18n import localize
from bot.states import AddItemFSM

router = Router()


@router.callback_query(F.data == 'add_item', HasPermissionFilter(permission=Permission.SHOP_MANAGE))
async def add_item_callback_handler(call: CallbackQuery, state):
    """
    Ask administrator for a new position name.
    """
    await call.message.edit_text(localize('admin.goods.add.prompt.name'), reply_markup=back("goods_management"))
    await state.set_state(AddItemFSM.waiting_item_name)


@router.message(AddItemFSM.waiting_item_name, F.text)
async def check_item_name_for_add(message: Message, state):
    """
    If position already exists — inform the user; otherwise save name and ask for description.
    """
    item_name = (message.text or "").strip()
    item = await check_item_cached(item_name)
    if item:
        await message.answer(
            localize('admin.goods.add.name.exists'),
            reply_markup=back('goods_management')
        )
        return

    await state.update_data(item_name=item_name)
    await message.answer(localize('admin.goods.add.prompt.description'), reply_markup=back('goods_management'))
    await state.set_state(AddItemFSM.waiting_item_description)


@router.message(AddItemFSM.waiting_item_description, F.text)
async def add_item_description(message: Message, state):
    """
    Save description and proceed to price input.
    """
    await state.update_data(item_description=(message.text or "").strip())
    await message.answer(localize('admin.goods.add.prompt.price', currency=EnvKeys.PAY_CURRENCY),
                         reply_markup=back('goods_management'))
    await state.set_state(AddItemFSM.waiting_item_price)


@router.message(AddItemFSM.waiting_item_price, F.text)
async def add_item_price(message: Message, state):
    """
    Validate price and ask for category.
    """
    price_text = (message.text or "").strip()
    if not price_text.isdigit():
        await message.answer(localize('admin.goods.add.price.invalid'), reply_markup=back('goods_management'))
        return

    await state.update_data(item_price=int(price_text))
    await message.answer(localize('admin.goods.add.prompt.category'), reply_markup=back('goods_management'))
    await state.set_state(AddItemFSM.waiting_category)


@router.message(AddItemFSM.waiting_category, F.text)
async def check_category_for_add_item(message: Message, state):
    """
    Category must exist; then ask about infinite mode.
    """
    category_name = (message.text or "").strip()
    category = await check_category_cached(category_name)
    if not category:
        await message.answer(
            localize('admin.goods.add.category.not_found'),
            reply_markup=back('goods_management')
        )
        return

    await state.update_data(item_category=category_name)
    await message.answer(
        localize('admin.goods.add.infinity.question'),
        reply_markup=question_buttons('infinity', 'goods_management')
    )
    await state.set_state(AddItemFSM.waiting_infinity)


@router.callback_query(F.data.startswith('infinity_'), AddItemFSM.waiting_infinity)
async def adding_value_to_position(call: CallbackQuery, state):
    """
    If infinite — wait for a single value.
    If not — collect multiple values until completion.
    """
    answer = call.data.split('_')[1]
    await state.update_data(is_infinity=(answer == 'yes'))

    if answer == 'no':
        # “Finish adding” button will appear after the first value is provided
        await call.message.edit_text(
            localize('admin.goods.add.values.prompt_multi'),
            reply_markup=back("goods_management")
        )
        await state.set_state(AddItemFSM.waiting_values)
    else:
        await call.message.edit_text(
            localize('admin.goods.add.single.prompt_value'),
            reply_markup=back('goods_management')
        )
        await state.set_state(AddItemFSM.waiting_single_value)


@router.message(AddItemFSM.waiting_values, F.text)
async def collect_item_value(message: Message, state):
    """
    Accumulate values in FSM state. After the first one — show a "Finish adding" button.
    """
    data = await state.get_data()
    values = data.get('item_values', [])
    value = (message.text or "")
    
    # Для текстовых товаров сохраняем просто текст
    values.append({
        'type': 'text',
        'value': value
    })
    
    await state.update_data(item_values=values)

    # Show progress + "Finish adding" button
    await message.answer(
        localize('admin.goods.add.values.added', value=value, count=len(values)),
        reply_markup=simple_buttons([
            (localize('btn.add_values_finish'), "finish_adding_items"),
            (localize('btn.back'), "goods_management")
        ], per_row=1)
    )


@router.callback_query(F.data == 'finish_adding_items', AddItemFSM.waiting_values)
async def finish_adding_items_callback_handler(call: CallbackQuery, state):
    """
    Create a position, add all collected values, notify group (if configured).
    """
    data = await state.get_data()
    item_name = data.get('item_name')
    item_description = data.get('item_description')
    item_price = data.get('item_price')
    category_name = data.get('item_category')
    raw_values: list[dict] = data.get("item_values", []) or []

    # Сначала создаем товар (позицию) в базе
    create_item(item_name, item_description, item_price, category_name)

    added = 0
    skipped_db_dup = 0
    skipped_batch_dup = 0
    skipped_invalid = 0
    seen_in_batch: set[str] = set()

    for item in raw_values:
        if item['type'] == 'text':
            v_norm = (item['value'] or "").strip()
            if not v_norm:
                skipped_invalid += 1
                continue

            # Duplicate within the current input batch
            if v_norm in seen_in_batch:
                skipped_batch_dup += 1
                continue
            seen_in_batch.add(v_norm)

            # Try to insert — False means it already exists in DB
            if add_values_to_item(item_name, v_norm, False):
                added += 1
            else:
                skipped_db_dup += 1

    text_lines = [
        localize('admin.goods.add.result.created'),
        localize('admin.goods.add.result.added', n=added)
    ]
    if skipped_db_dup:
        text_lines.append(localize('admin.goods.add.result.skipped_db_dup', n=skipped_db_dup))
    if skipped_batch_dup:
        text_lines.append(localize('admin.goods.add.result.skipped_batch_dup', n=skipped_batch_dup))
    if skipped_invalid:
        text_lines.append(localize('admin.goods.add.result.skipped_invalid', n=skipped_invalid))

    await call.message.edit_text("\n".join(text_lines), parse_mode="HTML", reply_markup=back("goods_management"))

    # Optionally notify a channel
    channel_url = EnvKeys.CHANNEL_URL or ""
    parsed = urlparse(channel_url)
    channel_username = (
                           parsed.path.lstrip('/')
                           if parsed.path else channel_url.replace("https://t.me/", "").replace("t.me/", "").lstrip('@')
                       ) or None
    if channel_username:
        try:
            await call.bot.send_message(
                chat_id=f"@{channel_username}",
                text=(
                    f"🎁 {localize('shop.group.new_upload')}\n"
                    f"🏷️ {localize('shop.group.item')}: <b>{item_name}</b>\n"
                    f"📦 {localize('shop.group.count')}: <b>{added}</b>"
                ),
                parse_mode='HTML'
            )
        except TelegramForbiddenError:
            await call.answer(localize("errors.channel.telegram_forbidden_error", channel=channel_username))
        except TelegramNotFound:
            await call.answer(localize("errors.channel.telegram_not_found", channel=channel_username))
        except TelegramBadRequest as e:
            await call.answer(localize("errors.channel.telegram_bad_request", e=e))

    admin_info = await call.message.bot.get_chat(call.from_user.id)
    audit_logger.info(
        f'Admin {call.from_user.id} ({admin_info.first_name}) created a new item "{item_name}"'
    )
    await state.clear()


@router.message(AddItemFSM.waiting_single_value, F.document)
async def finish_adding_file_item(message: Message, state):
    """
    Create a position and add one "infinite" file. Notify group (if configured).
    """
    data = await state.get_data()
    item_name = data.get('item_name')
    item_description = data.get('item_description')
    item_price = data.get('item_price')
    category_name = data.get('item_category')

    try:
        # Скачиваем файл
        document = message.document
        file_info = await message.bot.get_file(document.file_id)
        downloaded_file = await message.bot.download_file(file_info.file_path)
        file_bytes = downloaded_file.read()
        
        # Создаем позицию
        create_item(item_name, item_description, item_price, category_name)
        
        # Добавляем файл как бесконечный товар
        success = add_values_to_item(
            item_name=item_name,
            value=message.caption or document.file_name or "Файл",
            is_infinity=True,
            is_file=True,
            file_data=file_bytes,
            file_name=document.file_name or "file.ovpn",
            mime_type=document.mime_type or "application/octet-stream",
            file_size=document.file_size or len(file_bytes)
        )
        
        if success:
            success_message = f"✅ Позиция создана, файл '{document.file_name}' добавлен"
        else:
            success_message = "❌ Не удалось добавить файл (возможно, уже существует)"
        
    except Exception as e:
        # Очищаем сообщение об ошибке от бинарных данных
        error_msg = str(e).split('\n')[0]  # Берем только первую строку
        await message.answer(f"❌ Ошибка при загрузке файла: {error_msg}", reply_markup=back('goods_management'))
        return

    # Optionally notify a channel
    channel_url = EnvKeys.CHANNEL_URL or ""
    parsed = urlparse(channel_url)
    channel_username = (
                           parsed.path.lstrip('/')
                           if parsed.path else channel_url.replace("https://t.me/", "").replace("t.me/", "").lstrip('@')
                       ) or None
    if channel_username:
        try:
            await message.bot.send_message(
                chat_id=f"@{channel_username}",
                text=(
                    f"🎁 {localize('shop.group.new_upload')}\n"
                    f"🏷️ {localize('shop.group.item')}: <b>{item_name}</b>\n"
                    f"📦 {localize('shop.group.count')}: <b>∞</b>"
                ),
                parse_mode='HTML'
            )
        except TelegramForbiddenError:
            await message.answer(localize("errors.channel.telegram_forbidden_error", channel=channel_username))
        except TelegramNotFound:
            await message.answer(localize("errors.channel.telegram_not_found", channel=channel_username))
        except TelegramBadRequest as e:
            await message.answer(localize("errors.channel.telegram_bad_request", e=e))

    await message.answer(success_message, reply_markup=back('goods_management'))
    admin_info = await message.bot.get_chat(message.from_user.id)
    audit_logger.info(
        f'Admin {message.from_user.id} ({admin_info.first_name}) created an infinite file item "{item_name}"'
    )
    await state.clear()


@router.message(AddItemFSM.waiting_single_value, F.text)
async def finish_adding_text_item(message: Message, state):
    """
    Create a position and add one "infinite" text value. Notify group (if configured).
    """
    data = await state.get_data()
    item_name = data.get('item_name')
    item_description = data.get('item_description')
    item_price = data.get('item_price')
    category_name = data.get('item_category')

    single_value = (message.text or "").strip()
    if not single_value:
        await message.answer(localize('admin.goods.add.single.empty'), reply_markup=back('goods_management'))
        return

    # 1) Create position
    create_item(item_name, item_description, item_price, category_name)
    # 2) Add 1 "infinite" value
    add_values_to_item(item_name, single_value, True)
    
    success_message = localize('admin.goods.add.single.created')

    # 3) Optionally notify a channel
    channel_url = EnvKeys.CHANNEL_URL or ""
    parsed = urlparse(channel_url)
    channel_username = (
                           parsed.path.lstrip('/')
                           if parsed.path else channel_url.replace("https://t.me/", "").replace("t.me/", "").lstrip('@')
                       ) or None
    if channel_username:
        try:
            await message.bot.send_message(
                chat_id=f"@{channel_username}",
                text=(
                    f"🎁 {localize('shop.group.new_upload')}\n"
                    f"🏷️ {localize('shop.group.item')}: <b>{item_name}</b>\n"
                    f"📦 {localize('shop.group.count')}: <b>∞</b>"
                ),
                parse_mode='HTML'
            )
        except TelegramForbiddenError:
            await message.answer(localize("errors.channel.telegram_forbidden_error", channel=channel_username))
        except TelegramNotFound:
            await message.answer(localize("errors.channel.telegram_not_found", channel=channel_username))
        except TelegramBadRequest as e:
            await message.answer(localize("errors.channel.telegram_bad_request", e=e))

    await message.answer(success_message, reply_markup=back('goods_management'))
    admin_info = await message.bot.get_chat(message.from_user.id)
    audit_logger.info(
        f'Admin {message.from_user.id} ({admin_info.first_name}) created an infinite text item "{item_name}"'
    )
    await state.clear()


# Новый обработчик для загрузки файлов при добавлении обычных товаров
@router.message(AddItemFSM.waiting_values, F.document)
async def collect_file_value(message: Message, state):
    """
    Handle file upload for regular (non-infinite) items.
    Files are added immediately to database, not stored in state.
    """
    data = await state.get_data()
    item_name = data.get('item_name')
    
    if not item_name:
        await message.answer("❌ Ошибка: название товара не найдено", reply_markup=back('goods_management'))
        return
    
    try:
        # Скачиваем файл
        document = message.document
        file_info = await message.bot.get_file(document.file_id)
        downloaded_file = await message.bot.download_file(file_info.file_path)
        file_bytes = downloaded_file.read()
        
        # Сначала создаем товар (если еще не создан)
        item = await check_item_cached(item_name)
        if not item:
            # Получаем остальные данные
            item_description = data.get('item_description')
            item_price = data.get('item_price')
            category_name = data.get('item_category')
            
            if not all([item_description, item_price, category_name]):
                await message.answer(
                    "❌ Не все данные товара заполнены. Начните добавление заново.",
                    reply_markup=back('goods_management')
                )
                return
            
            create_item(item_name, item_description, item_price, category_name)
        
        # Добавляем файл в базу
        success = add_values_to_item(
            item_name=item_name,
            value=message.caption or document.file_name or "Файл",
            is_infinity=False,
            is_file=True,
            file_data=file_bytes,
            file_name=document.file_name or "file.ovpn",
            mime_type=document.mime_type or "application/octet-stream",
            file_size=document.file_size or len(file_bytes)
        )
        
        if success:
            # Получаем текущее количество товаров
            from bot.database.methods import select_item_values_amount
            count = select_item_values_amount(item_name)
            
            await message.answer(
                f"✅ Файл '{document.file_name}' добавлен. Всего товаров: {count}\n\n"
                f"Можете добавить еще файлы или текстовые значения, либо нажмите 'Завершить добавление'.",
                reply_markup=simple_buttons([
                    (localize('btn.add_values_finish'), "finish_adding_items"),
                    (localize('btn.back'), "goods_management")
                ], per_row=1)
            )
        else:
            await message.answer(
                f"❌ Не удалось добавить файл '{document.file_name}' (возможно, уже существует)",
                reply_markup=simple_buttons([
                    (localize('btn.add_values_finish'), "finish_adding_items"),
                    (localize('btn.back'), "goods_management")
                ], per_row=1)
            )
            
    except Exception as e:
        # Очищаем сообщение об ошибке от бинарных данных
        error_msg = str(e).split('\n')[0]  # Берем только первую строку
        await message.answer(
            f"❌ Ошибка при загрузке файла: {error_msg}",
            reply_markup=simple_buttons([
                (localize('btn.add_values_finish'), "finish_adding_items"),
                (localize('btn.back'), "goods_management")
            ], per_row=1)
        )
