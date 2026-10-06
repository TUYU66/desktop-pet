import { useState, useEffect, useCallback, useRef } from 'react';
import { Table, Button, Modal, Upload, App, Tag, Card, Input, Alert } from 'antd';
import { UploadOutlined } from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import { getFirmwareList, uploadFirmware, deleteFirmware } from '../../api/ota';
import dayjs from 'dayjs';

export default function OtaList() {
  const [data, setData] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(20);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState('');
  const generation = useRef(0);
  const [uploadOpen, setUploadOpen] = useState(false);
  const { message } = App.useApp();

  const fetchData = useCallback(async () => {
    const token = ++generation.current;
    setLoading(true);
    try {
      const res: any = await getFirmwareList(page, size);
      if (token !== generation.current) return;
      const count = Number(res.data?.total || 0);
      setTotal(count);
      if (page > 1 && (page - 1) * size >= count) setPage(Math.max(1, Math.ceil(count / size)));
      setData(res.data?.records || []);
      setError('');
    } catch (e) {
      if (token === generation.current) setError(e instanceof Error ? e.message : '固件列表读取失败');
    } finally {
      if (token === generation.current) setLoading(false);
    }
  }, [page, size]);

  useEffect(() => { void fetchData(); return () => { ++generation.current; }; }, [fetchData]);

  const handleUpload = async (values: { version: string; boardType: string; file: File }) => {
    try {
      await uploadFirmware(values.version, '', values.boardType, 0, values.file);
      message.success('上传成功');
      setUploadOpen(false);
      fetchData();
    } catch {
      message.error('上传失败');
    }
  };

  const handleDelete = (id: number) => {
    Modal.confirm({
      title: '确认删除',
      onOk: async () => {
        try {
          await deleteFirmware(id);
          message.success('已删除');
          fetchData();
        } catch {
          message.error('删除失败');
        }
      },
    });
  };

  const columns: ColumnsType<any> = [
    { title: '版本', dataIndex: 'version', key: 'version', width: 100 },
    { title: '板型', dataIndex: 'boardType', key: 'boardType', width: 180 },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 80,
      render: (s: number) => (
        <Tag color={s === 1 ? 'green' : 'default'}>{s === 1 ? '启用' : '禁用'}</Tag>
      ),
    },
    {
      title: '文件大小',
      dataIndex: 'fileSize',
      key: 'fileSize',
      width: 100,
      render: (s: number) => (s ? `${(s / 1024 / 1024).toFixed(1)} MB` : '-'),
    },
    {
      title: '创建时间',
      dataIndex: 'createDate',
      key: 'createDate',
      width: 160,
      render: (t: string) => (t ? dayjs(t).format('YYYY-MM-DD HH:mm') : '-'),
    },
    {
      title: '下载次数',
      dataIndex: 'downloadCount',
      key: 'downloadCount',
      width: 80,
    },
    {
      title: '操作',
      key: 'action',
      width: 80,
      render: (_, r) => (
        <Button type="link" danger onClick={() => handleDelete(r.id)}>
          删除
        </Button>
      ),
    },
  ];

  return (
    <div className="utility-page page-width"><div className="page-heading"><div><h1>固件管理</h1><p>查看固件版本，上传与管理设备升级文件。</p></div></div>
      {error && <Alert type="error" showIcon title={error} action={<Button onClick={() => void fetchData()}>重试</Button>} />}
      <Card
        style={{
          borderRadius: 16,
          border: 'none',
          boxShadow: '0 1px 3px rgba(0,0,0,0.04)',
        }}
      >
        <div
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            marginBottom: 16,
          }}
        >
          <span style={{ fontSize: 15, fontWeight: 600 }}>固件列表</span>
          <Button
            type="primary"
            icon={<UploadOutlined />}
            onClick={() => setUploadOpen(true)}
          >
            上传固件
          </Button>
        </div>
        <Table scroll={{ x: 700 }}
          rowKey="id"
          columns={columns}
          dataSource={data}
          loading={loading}
          pagination={{ current: page, pageSize: size, total, showSizeChanger: true,
            onChange: (next, nextSize) => { setPage(nextSize === size ? next : 1); setSize(nextSize); } }}
          style={{ borderRadius: 12 }}
        />
      </Card>

      <Modal
        title="上传固件"
        open={uploadOpen}
        onCancel={() => setUploadOpen(false)}
        footer={null}
        width={460}
      >
        <UploadForm onFinish={handleUpload} />
      </Modal>
    </div>
  );
}

function UploadForm({
  onFinish,
}: {
  onFinish: (v: any) => Promise<void>;
}) {
  const [version, setVersion] = useState('');
  const [boardType, setBoardType] = useState('bread-compact-wifi-lcd');
  const [file, setFile] = useState<File | null>(null);
  const [loading, setLoading] = useState(false);
  const { message } = App.useApp();

  const submit = async () => {
    if (!version || !file) {
      message.warning('请填写版本号和固件文件');
      return;
    }
    setLoading(true);
    try {
      await onFinish({ version, boardType, file });
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      <Input
        placeholder="版本号"
        value={version}
        onChange={(e) => setVersion(e.target.value)}
      />
      <Input
        placeholder="板型"
        value={boardType}
        onChange={(e) => setBoardType(e.target.value)}
      />
      <Upload
        beforeUpload={(f) => {
          setFile(f);
          return false;
        }}
        maxCount={1}
      >
        <Button icon={<UploadOutlined />}>选择固件文件</Button>
      </Upload>
      {file && <span style={{ color: '#6b7280', fontSize: 13 }}>{file.name}</span>}
      <Button type="primary" onClick={submit} loading={loading}>
        上传
      </Button>
    </div>
  );
}
