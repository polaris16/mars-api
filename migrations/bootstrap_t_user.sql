-- 仅用于全新空库。创建第一条 Alembic 迁移依赖的 t_user 原始结构。
BEGIN;

CREATE TABLE public.t_user (
    id BIGSERIAL PRIMARY KEY,
    username VARCHAR(20) NOT NULL,
    display_name VARCHAR(100),
    phone VARCHAR(20) NOT NULL,
    sex SMALLINT DEFAULT 0,
    birth_date DATE,
    tags TEXT[] DEFAULT '{}',
    user_role SMALLINT DEFAULT 0,
    created_by BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_by BIGINT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    is_del BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE UNIQUE INDEX uk_user_username ON public.t_user (username) WHERE is_del = FALSE;
-- 首次注册迁移会把此普通索引换成未删除手机号的唯一索引。
CREATE INDEX idx_user_phone ON public.t_user (phone) WHERE is_del = FALSE;
CREATE INDEX idx_user_tags ON public.t_user USING GIN (tags);

COMMENT ON TABLE public.t_user IS '用户信息表';
COMMENT ON COLUMN public.t_user.id IS '主键ID';
COMMENT ON COLUMN public.t_user.username IS '登录用户名/账号';
COMMENT ON COLUMN public.t_user.display_name IS '显示名称/昵称';
COMMENT ON COLUMN public.t_user.phone IS '手机号码';
COMMENT ON COLUMN public.t_user.sex IS '性别 (0:未知, 1:男, 2:女)';
COMMENT ON COLUMN public.t_user.birth_date IS '出生日期';
COMMENT ON COLUMN public.t_user.tags IS '用户标签数组';
COMMENT ON COLUMN public.t_user.user_role IS '用户角色 (0:普通用户, 1:企业用户)';
COMMENT ON COLUMN public.t_user.created_by IS '创建人ID';
COMMENT ON COLUMN public.t_user.created_at IS '创建时间';
COMMENT ON COLUMN public.t_user.updated_by IS '更新人ID';
COMMENT ON COLUMN public.t_user.updated_at IS '更新时间';
COMMENT ON COLUMN public.t_user.is_del IS '是否删除 (false:未删除, true:已删除)';

COMMIT;
