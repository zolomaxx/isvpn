from datetime import datetime
from decimal import Decimal
from random import randint
import logging

from bot.database.models import User, ItemValues, Goods, BoughtGoods, Payments, Operations
from bot.database import Database
from bot.misc import EnvKeys

logger = logging.getLogger(__name__)

def buy_item_transaction(telegram_id: int, item_name: str) -> tuple[bool, str, dict | None]:
    """
    Complete transactional purchase of goods with checks and locks.
    Returns: (success, message, purchase_data)
    """
    with Database().session() as s:
        try:
            # Starting the transaction
            s.begin()

            # 1. Block the user to check the balance
            user = s.query(User).filter(
                User.telegram_id == telegram_id
            ).with_for_update().one_or_none()

            if not user:
                s.rollback()
                logger.warning(f"User {telegram_id} not found")
                return False, "user_not_found", None

            # 2. Get information about the product
            goods = s.query(Goods).filter(
                Goods.name == item_name
            ).with_for_update().one_or_none()

            if not goods:
                s.rollback()
                logger.warning(f"Item {item_name} not found")
                return False, "item_not_found", None

            price = Decimal(str(goods.price))

            # 3. Checking the balance
            if user.balance < price:
                s.rollback()
                logger.warning(f"User {telegram_id} insufficient funds: {user.balance} < {price}")
                return False, "insufficient_funds", None

            # 4. Get and lock the first available item value (skip locked)
            item_value = s.query(ItemValues).filter(
                ItemValues.item_name == item_name
            ).with_for_update(skip_locked=True).first()

            if not item_value:
                s.rollback()
                logger.warning(f"No items in stock for {item_name}")
                return False, "out_of_stock", None

            # 5. If the product is not infinite, remove it
            if not item_value.is_infinity:
                s.delete(item_value)

            # 6. Deduct balance
            user.balance -= price

            # 7. Create purchase record
            # Determine value to store in bought_goods
            bought_value = item_value.value
            if item_value.is_file and item_value.file_name:
                bought_value = item_value.file_name
                
            bought_item = BoughtGoods(
                item_name=item_name,
                value=bought_value,
                price=price,
                buyer_id=telegram_id,
                bought_datetime=datetime.now(),
                unique_id=randint(1_000_000_000, 9_999_999_999),
                # Copy file information if it's a file
                is_file=item_value.is_file,
                file_data=item_value.file_data,
                file_name=item_value.file_name,
                mime_type=item_value.mime_type,
                file_size=item_value.file_size
            )
            s.add(bought_item)

            # 8. Commit transaction
            s.commit()
            
            logger.info(f"User {telegram_id} bought item {item_name} for {price}")

            return True, "success", {
                "item_name": item_name,
                "value": bought_value,
                "is_file": item_value.is_file,
                "file_data": item_value.file_data,
                "file_name": item_value.file_name,
                "mime_type": item_value.mime_type,
                "file_size": item_value.file_size,
                "price": float(price),
                "new_balance": float(user.balance),
                "unique_id": bought_item.unique_id
            }

        except Exception as e:
            s.rollback()
            logger.error(f"Transaction error for user {telegram_id}, item {item_name}: {e}")
            return False, f"transaction_error: {str(e)}", None
