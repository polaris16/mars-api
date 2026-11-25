"""[新增] 语音片段与可复用媒体对象的关联。"""

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import UUID

from app.models.base import Base


class FragmentAttachment(Base):
    __tablename__ = "t_fragment_attachment"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="t_fragment_attachment_pkey"),
        CheckConstraint("sort_order >= 0", name="ck_t_fragment_attachment_sort_order"),
        # 每条附件关联可以独立保留，软删除后仍保留其操作标识。
        CheckConstraint(
            "client_file_id IS NULL OR created_by IS NOT NULL",
            name="ck_t_fragment_attachment_client_file_creator",
        ),
        Index(
            "idx_t_fragment_attachment_active_asset",
            "fragment_id",
            "media_asset_id",
            postgresql_where=text("is_del = false"),
        ),
        Index(
            "uq_t_fragment_attachment_client_file",
            "fragment_id",
            "created_by",
            "client_file_id",
            unique=True,
            postgresql_where=text("client_file_id IS NOT NULL"),
        ),
        Index("idx_t_fragment_attachment_order", "fragment_id", "sort_order", "id"),
        {"comment": "语音片段与多媒体素材关联表"},
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True, comment="关联ID")
    fragment_id = Column(
        BigInteger,
        ForeignKey(
            "t_memory_fragment.id", ondelete="CASCADE", name="fk_t_fragment_attachment_fragment"
        ),
        nullable=False,
        comment="语音片段ID",
    )
    media_asset_id = Column(
        BigInteger,
        ForeignKey(
            "t_media_asset.id", ondelete="CASCADE", name="fk_t_fragment_attachment_media_asset"
        ),
        nullable=False,
        comment="媒体对象ID",
    )
    # 附件的提供者记录在关联表，不放在可被多人复用的文件对象上。
    contributor_id = Column(
        BigInteger,
        ForeignKey(
            "t_memory_contributor.id",
            ondelete="RESTRICT",
            name="fk_t_fragment_attachment_contributor",
        ),
        nullable=False,
        comment="添加附件的提供者ID",
    )
    # 兼容没有操作标识的历史附件和既有创建流程。
    client_file_id = Column(
        UUID(as_uuid=True), nullable=True, comment="客户端新建附件关联的操作标识"
    )
    caption = Column(String(200), nullable=True, comment="附件说明")
    sort_order = Column(Integer, nullable=False, server_default=text("0"), comment="展示顺序")
    created_by = Column(BigInteger, nullable=True, comment="创建人ID")
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        comment="创建时间",
    )
    updated_by = Column(BigInteger, nullable=True, comment="更新人ID")
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        comment="更新时间",
    )
    is_del = Column(Boolean, nullable=False, server_default=text("false"), comment="是否删除")
