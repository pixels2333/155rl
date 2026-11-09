# 高质量测试与反模式示范 (Good and Bad Tests)

本参考文档通过正反两组具象代码对比，展示什么是“耐重构、测行为”的高价值测试，以及应当极力规避的典型坏测试反模式。

---

## 1. 优秀测试典范 (Good Tests)

**集成契约风格 (Integration-style)**：通过真实的公共接口测试外部可观测的行为，不 Mock 内部实现细节。

```typescript
// ✅ 优秀测试：测试对用户和调用方真正有价值的外部行为
test("用户可以使用有效购物车完成结账", async () => {
  const cart = createCart();
  cart.add(product);
  
  const result = await checkout(cart, paymentMethod);
  
  expect(result.status).toBe("confirmed");
});
```

### 优秀测试的核心特征
- **聚焦业务价值**：验证用户或上层调用者真正关心的业务结果，而不是中间流转。
- **仅走公共接口**：只通过公共 API 进行交互，不碰内部状态。
- **具备重构韧性**：当内部代码结构彻底重构但外部行为未变时，测试应当 100% 保持通过。
- **陈述“做什么 (WHAT)”而非“怎么做 (HOW)”**：测试命名即业务规格说明书。
- **单一逻辑断言**：每个用例专注于验证一个核心业务推论。

---

## 2. 劣质测试反模式 (Bad Tests)

### 反模式 1：实现细节耦合 (Implementation-detail tests)
深度绑定内部代码结构、私有状态或调用序列。

```typescript
// ❌ 劣质测试：与内部实现细节严重耦合
test("结账时会调用 paymentService.process 方法", async () => {
  const mockPayment = jest.mock(paymentService);
  
  await checkout(cart, payment);
  
  // 危险：测试关注的是“内部如何实现”，而非“结账是否成功”
  expect(mockPayment.process).toHaveBeenCalledWith(cart.total);
});
```

#### 常见警示信号 (Red Flags)
- 大量 Mock 服务内部的协作对象。
- 测试私有方法、内部字段。
- 断言内部方法的调用次数、调用顺序（如 `toHaveBeenCalledTimes(1)`）。
- 一旦重构内部实现（例如将支付抽取为后台管道），业务逻辑完全没变，测试却大面积报错。
- 测试名称描述的是“怎么做”（如“检查调用了 X 对象的 Y 函数”）。

---

### 反模式 2：绕过公共接口进行旁路验证 (Side-channel verification)
不通过公共接口观察结果，而是偷偷绕到后方直接查询数据库或私有底层缓存。

```typescript
// ❌ 劣质测试：绕过接口，直接查库验证
test("createUser 会在数据库中插入一条记录", async () => {
  await createUser({ name: "Alice" });
  
  // 绕过服务接口直接查底层 DB 表结构
  const row = await db.query("SELECT * FROM users WHERE name = ?", ["Alice"]);
  expect(row).toBeDefined();
});

// ✅ 优秀测试：完全通过对外公开的业务接口验证持久化效果
test("createUser 成功后，可以通过接口重新获取该用户信息", async () => {
  const user = await createUser({ name: "Alice" });
  
  const retrieved = await getUser(user.id);
  expect(retrieved.name).toBe("Alice");
});
```

---

### 反模式 3：同义反复测试 (Tautological tests)
断言的预期结果是用与业务代码一模一样的方式重新计算出来的，或者断言一个常数等于自身。测试在结构上“必然通过”，永远无法发现真实逻辑错误。

```typescript
// ❌ 劣质测试：预期值使用了与业务逻辑完全相同的 reduce 逻辑重新算了一遍
test("calculateTotal 会累加所有商品项价格", () => {
  const items = [{ price: 10 }, { price: 5 }];
  // 与实现代码复制粘贴的相同逻辑：
  const expected = items.reduce((sum, i) => sum + i.price, 0);
  
  expect(calculateTotal(items)).toBe(expected);
});

// ✅ 优秀测试：预期值来自于独立的业务真值基准（独立常量、已知案例）
test("calculateTotal 计算指定清单的总金额正确", () => {
  expect(calculateTotal([{ price: 10 }, { price: 5 }])).toBe(15);
});
```
