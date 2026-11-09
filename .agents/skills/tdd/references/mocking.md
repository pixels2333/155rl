# Mocking 原则与可测性设计指南 (When to Mock)

在单元测试与集成测试中，滥用 Mock 会导致测试与实现细节深度耦合，使得代码重构极其脆弱。本指南阐明何时应当 Mock、何时禁止 Mock，以及如何设计天然易于 Mock 的接口。

---

## 1. 何时应当 Mock (When to Mock)

仅在**系统边界 (System Boundaries)** 处进行 Mock：

- **外部服务与第三方 API**：如支付网关（Stripe/支付宝）、邮件服务、短信验证码等。
- **持久化数据库**：在无法使用轻量级测试数据库（如 SQLite 内存库、TestContainers）时，才 Mock 数据访问边界；优先推荐真实测试库。
- **非确定性因素**：系统时钟（时间流逝）、随机数生成器、全局 GUID 生成。
- **外部操作系统文件/硬件交互**：文件系统 I/O、串口网络流（当需要模拟异常磁盘写入故障时）。

### 绝对禁止 Mock 的场景 (Don't Mock)

- ❌ **自己编写的业务领域类/模块**：不要在同一个服务内部将自己的协作对象 Mock 掉。
- ❌ **内部协作对象与私有方法**：不要测试私有成员或验证内部方法调用次序。
- ❌ **一切完全处于你控制之下的逻辑**：使用真实对象参与测试，才能真正验证组合逻辑与数据流转的正确性。

---

## 2. 面向可测性的接口设计 (Designing for Mockability)

在系统边界处，遵循以下两大模式使接口易于替换和测试：

### 原则 1：依赖注入 (Dependency Injection)
将外部依赖通过构造函数或参数显式传入，而不是在内部直接硬编码实例化：

```typescript
// ✅ 优秀设计：易于测试和替换
function processPayment(order: Order, paymentClient: PaymentClient) {
  return paymentClient.charge(order.total);
}

// ❌ 恶劣设计：硬编码实例化外部客户端，难以注入 Mock
function processPayment(order: Order) {
  const client = new StripeClient(process.env.STRIPE_KEY);
  return client.charge(order.total);
}
```

### 原则 2：优先使用领域 SDK 风格接口，避免泛型万能请求器
为每个具体的外部业务操作定义清晰的独立方法，而不是使用带有复杂条件判断的单一万能函数：

```typescript
// ✅ 优秀设计：每个业务方法独立，Mock 行为明确且具类型安全性
const api = {
  getUser: (id: string) => fetch(`/users/${id}`),
  getOrders: (userId: string) => fetch(`/users/${userId}/orders`),
  createOrder: (data: OrderData) => fetch('/orders', { method: 'POST', body: JSON.stringify(data) }),
};

// ❌ 恶劣设计：万能 fetcher 需要在 Mock 内部编写大量 if-else 条件判断
const api = {
  fetch: (endpoint: string, options: any) => fetch(endpoint, options),
};
```

#### SDK 风格的优势：
1. **单一职责**：每个 Mock 桩只需返回确定结构的数据，测试设置干净直观。
2. **零逻辑分发**：测试用例的前置设置（Arrange）中不再需要维护复杂的 URL 分支判定。
3. **调用可视化**：测试一目了然地展现了当前用例触发了哪些外部接口。
4. **编译期类型约束**：针对各端点具备完整的类型检查与自动补全保障。
