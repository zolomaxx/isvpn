"""Add file support to item_values table

Revision ID: add_file_support
Revises: 3f9d1b7a4a6c
Create Date: 2024-01-01 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers
revision = 'add_file_support'
down_revision = '3f9d1b7a4a6c'
branch_labels = None
depends_on = None


def upgrade():
    # Добавляем новые поля для файлов
    op.add_column('item_values', sa.Column('is_file', sa.Boolean(), nullable=False, server_default='false'))
    op.add_column('item_values', sa.Column('file_data', sa.LargeBinary(), nullable=True))
    op.add_column('item_values', sa.Column('file_name', sa.String(length=255), nullable=True))
    op.add_column('item_values', sa.Column('mime_type', sa.String(length=100), nullable=True))
    op.add_column('item_values', sa.Column('file_size', sa.Integer(), nullable=True))
    
    # Для bought_goods тоже добавляем поддержку файлов
    op.add_column('bought_goods', sa.Column('is_file', sa.Boolean(), nullable=False, server_default='false'))
    op.add_column('bought_goods', sa.Column('file_data', sa.LargeBinary(), nullable=True))
    op.add_column('bought_goods', sa.Column('file_name', sa.String(length=255), nullable=True))
    op.add_column('bought_goods', sa.Column('mime_type', sa.String(length=100), nullable=True))
    op.add_column('bought_goods', sa.Column('file_size', sa.Integer(), nullable=True))
    
    # Удаляем server_default после добавления
    op.alter_column('item_values', 'is_file', server_default=None)
    op.alter_column('bought_goods', 'is_file', server_default=None)


def downgrade():
    # Удаляем поля из bought_goods
    op.drop_column('bought_goods', 'file_size')
    op.drop_column('bought_goods', 'mime_type')
    op.drop_column('bought_goods', 'file_name')
    op.drop_column('bought_goods', 'file_data')
    op.drop_column('bought_goods', 'is_file')
    
    # Удаляем поля из item_values
    op.drop_column('item_values', 'file_size')
    op.drop_column('item_values', 'mime_type')
    op.drop_column('item_values', 'file_name')
    op.drop_column('item_values', 'file_data')
    op.drop_column('item_values', 'is_file')