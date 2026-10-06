import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Alert, App, Button, Card, Empty, Form, Input, Modal, Popconfirm, Select, Spin, Typography } from 'antd';
import { DeleteOutlined, EditOutlined, PlusOutlined } from '@ant-design/icons';
import dayjs from 'dayjs';
import { clearMemories, createMemory, deleteMemory, getMemories, getMemoryHistory, type MemoryHistoryRecord, type MemoryRecord, updateMemory } from '../../api/chat';
import { getBotConfig } from '../../api/user';
import './memory-list.css';

const CATEGORIES = [
  { value: 'profile', label: '个人信息', description: '身份与稳定背景' },
  { value: 'preference', label: '喜好', description: '长期喜恶与偏好' },
  { value: 'relationship', label: '关系', description: '重要的人与关系' },
  { value: 'event', label: '经历', description: '已经发生的重要经历' },
  { value: 'goal', label: '目标', description: '持续性的目标与计划' },
  { value: 'habit', label: '习惯', description: '重复出现的日常习惯' },
  { value: 'note', label: '其他', description: '其他主动保留的信息' },
];

interface MemoryFormValues { category: string; content: string; key?: string }

function memoryData(memory: MemoryRecord | null): Record<string, unknown> {
  try {
    const data: unknown = memory?.factJson ? JSON.parse(memory.factJson) : null;
    return data && typeof data === 'object' && !Array.isArray(data) ? data as Record<string, unknown> : {};
  } catch { return {}; }
}

function SemanticHistory({ memory }: { memory: MemoryRecord }) {
  const [entries, setEntries] = useState<MemoryHistoryRecord[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  const load = async () => {
    if (loading) return;
    setLoading(true); setFailed(false);
    try { setEntries((await getMemoryHistory(memory.id, memory.roleId)).data); }
    catch { setFailed(true); }
    finally { setLoading(false); }
  };
  const reasons: Record<string, string> = { new: '新增', changed: '真实变化', corrected: '纠错（旧值不代表真实历史）',
    refines: '补充陈述', completed: '目标完成', cancelled: '目标取消', invalidated: '作废' };
  return <details onToggle={event => { if (event.currentTarget.open && entries === null && !failed) void load(); }}>
    <summary>查看记忆维护历史</summary>
    {loading && <Spin size="small" />}
    {failed && <Button onClick={() => void load()}>读取失败，点击重试</Button>}
    {entries?.length === 0 && <p>没有历史记录</p>}
    {entries?.map(entry => {
      const old = memoryData({ factJson: entry.oldJson } as MemoryRecord);
      const next = memoryData({ factJson: entry.newJson } as MemoryRecord);
      return <div key={entry.id}>
        <p>{reasons[entry.reason] ?? entry.reason} · {entry.changedAt ? dayjs(entry.changedAt).format('YYYY-MM-DD HH:mm') : ''}</p>
        {typeof old.content === 'string' && <p>修改前：{old.content}</p>}
        {typeof next.content === 'string' && <p>修改后：{next.content}</p>}
        <p>原话：{entry.source}</p>
      </div>;
    })}
    {entries && entries.length >= 100 && <p>仅展示最近100条维护记录</p>}
  </details>;
}

function MemoryProvenance({ memory }: { memory: MemoryRecord }) {
  const fact = memoryData(memory);
  const source = memory.sourceType === 'manual' ? '手动保存' : fact.memoryMode === 'explicit' ? '主动要求记忆' : '自动记忆';
  return <>
    <div className="memory-row-meta">
      <span>{source}</span>
      {typeof fact.key === 'string' && <span>主题：{fact.key}</span>}
      {fact.status === 'closed' && <span>已结束</span>}
      {typeof fact.observedAt === 'string' && <span>陈述日期：{fact.observedAt}</span>}
      <time>{memory.updateDate ? `更新于 ${dayjs(memory.updateDate).format('YYYY年MM月DD日 HH:mm')}` : '更新时间未记录'}</time>
    </div>
    {typeof fact.source === 'string' && <details>
      <summary>查看记忆原话</summary>
      <Typography.Paragraph style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{fact.source}</Typography.Paragraph>
    </details>}
    <SemanticHistory key={`${memory.id}-${memory.version}`} memory={memory} />
  </>;
}

export default function MemoryList() {
  const { message } = App.useApp();
  const [form] = Form.useForm<MemoryFormValues>();
  const [roleId, setRoleId] = useState('default');
  const [roleName, setRoleName] = useState('桌面宠物');
  const [memories, setMemories] = useState<MemoryRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [loadFailed, setLoadFailed] = useState(false);
  const [loadError, setLoadError] = useState('');
  const [editing, setEditing] = useState<MemoryRecord | null>(null);
  const [modalOpen, setModalOpen] = useState(false);
  const [keyword, setKeyword] = useState('');
  const [categoryFilter, setCategoryFilter] = useState('all');
  const [view, setView] = useState<'category' | 'recent'>('category');
  const [hasLoaded, setHasLoaded] = useState(false);
  const inFlight = useRef<number | null>(null);
  const generation = useRef(0);
  const loadedRoleId = useRef('default');
  const mutating = useRef(false);
  const writable = hasLoaded && !loadFailed && !loading && !saving;
  const categoryOf = (memory: MemoryRecord) => CATEGORIES.find(category => category.value === memory.category) || CATEGORIES[CATEGORIES.length - 1];
  const filteredMemories = useMemo(() => {
    const query = keyword.trim().toLocaleLowerCase();
    return memories.filter(memory => {
      const category = CATEGORIES.find(item => item.value === memory.category) || CATEGORIES[CATEGORIES.length - 1];
      const fact = memoryData(memory);
      return (categoryFilter === 'all' || category.value === categoryFilter) &&
        (!query || [memory.content, category.label, fact.key, fact.source]
          .filter(value => typeof value === 'string').join(' ').toLocaleLowerCase().includes(query));
    }).sort((a, b) => {
      const dateA = a.updateDate || a.createDate ? dayjs(a.updateDate || a.createDate).valueOf() : 0;
      const dateB = b.updateDate || b.createDate ? dayjs(b.updateDate || b.createDate).valueOf() : 0;
      return (Number.isFinite(dateB) ? dateB : 0) - (Number.isFinite(dateA) ? dateA : 0) || b.id - a.id;
    });
  }, [memories, keyword, categoryFilter]);
  const groupedMemories = view === 'recent' ? [{ value: 'recent', label: '最近更新', description: '按最近更新时间排序', items: filteredMemories }] : CATEGORIES.map(category => ({
    ...category,
    items: filteredMemories.filter(memory =>
      memory.category === category.value ||
      (category.value === 'note' && !CATEGORIES.some(item => item.value === memory.category))),
  })).filter(group => group.items.length > 0);

  const load = useCallback(async () => {
    if (inFlight.current === generation.current) return;
    const token = ++generation.current;
    inFlight.current = token;
    setLoading(true);
    try {
      const configResponse = await getBotConfig();
      const config = configResponse.data;
      if (!config || typeof config !== 'object') throw new Error('角色配置未完整返回');
      const parsed = config.roles ? JSON.parse(config.roles) : [];
      if (!Array.isArray(parsed) || parsed.some(role => !role || typeof role !== 'object')) throw new Error('角色配置格式错误');
      const roles = parsed;
      const active = roles.find((role: { id?: string }) => role.id === config.activeRole) || roles[0];
      const activeRoleId = active?.id || config.activeRole || 'default';
      const response = await getMemories(activeRoleId);
      if (token !== generation.current) return;
      if (!Array.isArray(response.data) || response.data.some(memory => !memory || typeof memory.id !== 'number' || typeof memory.content !== 'string')) throw new Error('记忆列表未完整返回，请重新加载');
      if (loadedRoleId.current !== activeRoleId) { setEditing(null);setModalOpen(false); }
      loadedRoleId.current = activeRoleId;
      setRoleId(activeRoleId);
      const name = active ? active.name : config.name;
      setRoleName(typeof name === 'string' && name.trim() ? name.trim() : '桌面宠物');
      setMemories(response.data);
      setHasLoaded(true);setLoadFailed(false);setLoadError('');
    } catch (error) {
      if (token === generation.current) {
        setLoadFailed(true);
        setLoadError(error instanceof Error ? error.message : '无法读取记忆');
      }
    } finally {
      if (inFlight.current === token) inFlight.current = null;
      if (token === generation.current) setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); return () => { ++generation.current; }; }, [load]);

  const openCreate = () => {
    if (!writable) return;
    setEditing(null);
    form.setFieldsValue({ category: 'note', content: '', key: '' });
    setModalOpen(true);
  };

  const openEdit = (memory: MemoryRecord) => {
    if (!writable) return;
    setEditing(memory);
    form.setFieldsValue({ category: memory.category, content: memory.content, key: typeof memoryData(memory).key === 'string' ? String(memoryData(memory).key) : undefined });
    setModalOpen(true);
  };

  const save = async () => {
    if (!writable || mutating.current) return;
    mutating.current = true;
    setSaving(true);
    const token = generation.current;
    try {
      const values = await form.validateFields();
      if (token !== generation.current) { message.warning('记忆已重新加载，请核实内容后再保存');return; }
      if (editing) await updateMemory(editing.id, values.category, values.content.trim(), editing.version, values.key?.trim());
      else await createMemory(roleId, values.category, values.content.trim(), values.key?.trim());
      setModalOpen(false);
      message.success(editing ? '记忆已更新' : '记忆已添加');
      await load();
    } catch (error) {
      if ((error as { errorFields?: unknown }).errorFields) return;
      message.error(error instanceof Error ? error.message : '保存失败，请重试');
    } finally {
      mutating.current = false;
      setSaving(false);
    }
  };

  const remove = async (id: number) => {
    if (!writable || mutating.current) return;
    mutating.current = true;
    setSaving(true);
    try {
      await deleteMemory(id);
      setMemories(items => items.filter(item => item.id !== id));
      message.success('记忆已删除，之后的回答不会再读取它');
    } catch (error) {
      message.error(error instanceof Error ? error.message : '删除失败，请重试');
    } finally { mutating.current = false;setSaving(false); }
  };

  const clearAll = async () => {
    if (!writable || mutating.current) return;
    mutating.current = true;
    setSaving(true);
    try {
      await clearMemories(roleId);
      setMemories([]);
      setLoadFailed(false);
      message.success('已清空全部长期记忆');
    } catch (error) {
      message.error(error instanceof Error ? error.message : '清空失败，请重试');
    } finally { mutating.current = false;setSaving(false); }
  };

  return <div className="memory-page page-width">
    <div className="page-heading"><div><h1>长期记忆</h1><Typography.Paragraph type="secondary">
      {roleName} 会在每次回答前读取这里的内容。你可以补充、修正或删除任何一条。
    </Typography.Paragraph><Typography.Paragraph type="secondary">
      明确且有长期价值的个人信息、关系、喜好、习惯、目标和重要经历会自动记忆。其他无法归类的信息，仅在你明确要求时保存。日期表示当时的陈述，不会自动推算年龄或年级。
    </Typography.Paragraph></div><div><Button onClick={() => void load()} loading={loading} disabled={saving}>刷新</Button> <Button type="primary" icon={<PlusOutlined />} disabled={!writable} onClick={openCreate}>添加记忆</Button></div></div>

    {loadFailed && <Alert type="warning" showIcon title="记忆读取失败" description={`${loadError}${hasLoaded ? '。下方保留上次成功读取的内容，尚未更新；重新加载成功后才能编辑。' : '。暂时无法确认是否有记忆，请重新加载。'}`}
      action={<Button onClick={() => void load()} loading={loading}>重新加载</Button>} />}
    {loading && <div className="memory-loading"><Spin />{hasLoaded && <span>正在更新，下方为上次读取的内容。</span>}</div>}
    {hasLoaded && <>
      <div className="memory-filters">
        <Input.Search aria-label="搜索记忆" placeholder="搜索内容、主题或原话" value={keyword} allowClear onChange={event => setKeyword(event.target.value)} />
        <Select aria-label="筛选记忆分类" value={categoryFilter} onChange={setCategoryFilter} options={[{ value: 'all', label: '全部分类' }, ...CATEGORIES.map(({ value, label }) => ({ value, label }))]} />
        <Select aria-label="记忆查看方式" value={view} onChange={setView} options={[{ value: 'category', label: '按分类查看' }, { value: 'recent', label: '最近更新' }]} />
      </div>
      <div className="memory-summary"><span>{loadFailed || loading ? '上次读取' : '当前'}共 {memories.length} 条{keyword.trim() || categoryFilter !== 'all' ? ` · 匹配 ${filteredMemories.length} 条` : ''}</span>{memories.length > 0 && <Popconfirm key={roleId}
        title="清空全部长期记忆？" description="清空后无法恢复，之后的回答也不会再读取这些内容。"
        okText="确认清空" cancelText="取消" okButtonProps={{ danger: true }} onConfirm={clearAll}>
        <Button danger type="text" disabled={!writable}>清空全部</Button>
      </Popconfirm>}</div>
      {memories.length === 0 ? <Card className="memory-empty"><Empty description={loadFailed || loading ? '上次读取时没有长期记忆' : '还没有长期记忆'}><Button disabled={!writable} onClick={openCreate}>手动添加第一条</Button></Empty></Card> : filteredMemories.length === 0 ?
        <Card className="memory-empty"><Empty description="没有匹配的记忆"><Button onClick={() => { setKeyword('');setCategoryFilter('all'); }}>清除筛选</Button></Empty></Card> :
        <div className="memory-groups">{groupedMemories.map(group => <section
          className={`memory-group memory-group-${group.value}`} key={group.value} aria-labelledby={`memory-${group.value}`}>
          <header className="memory-group-heading"><span className="memory-category-mark" aria-hidden="true" />
            <div><h2 id={`memory-${group.value}`}>{group.label}</h2><p>{group.description}</p></div>
            <span className="memory-category-count">{group.items.length} 条</span>
          </header>
          <div className="memory-group-list">{group.items.map(memory => <article className="memory-row" key={memory.id}>
            <div className="memory-row-content">{view === 'recent' && <span className="memory-row-category">{categoryOf(memory).label}</span>}<p>{memory.content}</p><MemoryProvenance memory={memory} /></div>
            <div className="memory-row-actions"><Button type="text" disabled={!writable} icon={<EditOutlined />} onClick={() => openEdit(memory)}>编辑</Button>
              <Popconfirm title="删除这条记忆？" description={`删除后，${roleName}将不再读取这条内容。`} okText="删除" cancelText="取消" onConfirm={() => remove(memory.id)}>
                <Button type="text" disabled={!writable} danger icon={<DeleteOutlined />}>删除</Button>
              </Popconfirm></div>
          </article>)}</div>
        </section>)}</div>}
    </>}

    <Modal title={editing ? '编辑记忆' : '添加记忆'} open={modalOpen} confirmLoading={saving} okText="保存" cancelText="取消"
      onOk={() => void save()} okButtonProps={{ disabled: !writable }} onCancel={() => !saving && setModalOpen(false)} destroyOnHidden>
      <Form form={form} layout="vertical" preserve={false} disabled={!writable}>
        <Form.Item name="key" label="记忆主题" extra="每条保留一个独立事实，例如猫和狗的数量应分别保存。" rules={[{ required: true, whitespace: true, message: '请输入记忆主题' }, { max: 120 }]}>
          <Input maxLength={120} placeholder="例如：用户的猫数量" />
        </Form.Item>
        <Form.Item name="category" label="分类" rules={[{ required: true }]}><Select options={CATEGORIES.map(({ value, label }) => ({ value, label }))} /></Form.Item>
        <Form.Item name="content" label="记忆内容" rules={[{ required: true, whitespace: true, message: '请输入记忆内容' }, { max: 2000, message: '最多 2000 字' }]}>
          <Input.TextArea rows={5} maxLength={2000} showCount placeholder="例如：用户喜欢冷蓝色界面。" />
        </Form.Item>
      </Form>
    </Modal>
  </div>;
}
