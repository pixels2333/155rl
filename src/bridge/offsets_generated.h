// 自动生成,请勿手改:scripts/gen_offsets_header.py <- configs/offsets.yaml
#ifndef TH155_OFFSETS_GENERATED_H
#define TH155_OFFSETS_GENERATED_H

#define TH155_EXPECTED_SIZE   5287640u
#define TH155_EXPECTED_SHA256 "fc6708094b53afa3c00584ecb6e9be14bbf57c2fcd568ef8017af27f8b8ee1f8"

// --- engine ---
#define O_VM_GLOBAL_RVA       0x004dace4u
#define O_VM_ROOTTABLE        0x00000030u
#define O_FRAME_COUNTER_RVA   0x004dace0u

// --- squirrel ---
#define O_RAW_TYPE_MASK       0x00ffffffu
#define O_TAG_NULL            0x01000001u
#define O_TAG_INT             0x05000002u
#define O_TAG_FLOAT           0x05000004u
#define O_TAG_BOOL            0x01000008u
#define O_TAG_STRING          0x08000010u
#define O_TAG_TABLE           0x0a000020u
#define O_TAG_ARRAY           0x08000040u
#define O_TAG_INSTANCE        0x0a008000u
#define O_TAG_CLOSURE         0x08000100u
#define O_OBJ_VALUE           0x04u

#define O_STR_LENGTH          0x00000014u
#define O_STR_DATA            0x0000001cu

#define O_TABLE_NODES         0x00000020u
#define O_TABLE_NUMNODES      0x00000024u
#define O_TABLE_NODE_STRIDE   0x00000014u
#define O_TABLE_NODE_VTAG     0x00u
#define O_TABLE_NODE_VALUE    0x04u
#define O_TABLE_NODE_KTAG     0x08u
#define O_TABLE_NODE_KEY      0x0Cu
#define O_TABLE_NODE_NEXT     0x10u

#define O_ARRAY_VALUES        0x00000018u
#define O_ARRAY_SIZE          0x0000001cu

#define O_INST_CLASS          0x0000001cu
#define O_INST_USERPTR        0x00000020u
#define O_INST_VALUES         0x0000002cu
#define O_INST_STRIDE         0x00000008u

#define O_CLASS_MEMBERS       0x00000018u
#define O_CLASS_DEFAULTS      0x00000020u
#define O_CLASS_METHODS       0x0000002cu
#define O_MEMBER_FIELD_FLAG   0x02000000u
#define O_MEMBER_METHOD_FLAG  0x01000000u

// --- native: Manbow::Actor2D ---
#define O_ACTOR_X             0x0000000cu
#define O_ACTOR_Y             0x00000010u
#define O_ACTOR_ID            0x00000018u
#define O_ACTOR_VX            0x0000002cu
#define O_ACTOR_VY            0x00000030u
#define O_ACTOR_DIR           0x00000038u
#define O_ACTOR_ANIM          0x0000003cu
#define O_ACTOR_RX            0x0000005cu
#define O_ACTOR_RY            0x00000060u
#define O_ACTOR_RZ            0x00000064u
#define O_ACTOR_SX            0x00000050u
#define O_ACTOR_SY            0x00000054u
#define O_ACTOR_SZ            0x00000058u
#define O_ACTOR_GROUP         0x000000dcu
#define O_ACTOR_CGROUP        0x0000007cu
#define O_ACTOR_CMASK         0x00000080u
#define O_ACTOR_CBGROUP       0x00000084u
#define O_ACTOR_CBMASK        0x00000088u
#define O_ACTOR_BOX_L         0x0000001cu
#define O_ACTOR_BOX_T         0x00000020u
#define O_ACTOR_BOX_R         0x00000024u
#define O_ACTOR_BOX_B         0x00000028u
#define O_ACTOR_HIT_L         0x0000008cu
#define O_ACTOR_HIT_T         0x00000090u
#define O_ACTOR_HIT_R         0x00000094u
#define O_ACTOR_HIT_B         0x00000098u

#define O_ANIM_MOTION         0x0000001cu
#define O_ANIM_TAKE           0x00000020u
#define O_ANIM_FRAME          0x00000024u

// --- native: Manbow::InputMulti ---
#define O_INPUT_X             0x00000004u
#define O_INPUT_Y             0x00000008u
#define O_INPUT_B0            0x0000000cu
#define O_INPUT_B1            0x00000010u
#define O_INPUT_B2            0x00000014u
#define O_INPUT_B3            0x00000018u
#define O_INPUT_B4            0x0000001cu
#define O_INPUT_B5            0x00000020u
#define O_INPUT_B10           0x00000034u
#define O_INPUT_UPDATE_RVA    0x0006f360u
#define O_INPUT_VTABLE_RVA    0x00445360u

// --- native: Manbow::InputCommand ---
#define O_CMD_B0              0x0000000cu
#define O_CMD_B1              0x00000010u
#define O_CMD_B2              0x00000014u
#define O_CMD_B3              0x00000018u
#define O_CMD_B4              0x0000001cu
#define O_CMD_B5              0x00000020u

// --- native: Actor2DProcGroup (弹幕容器) ---
#define O_PG_HEAD             0x00000000u
#define O_PG_NEXT             0x00000000u
#define O_PG_PREV             0x00000004u
#define O_PG_PTR              0x00000008u

#endif // TH155_OFFSETS_GENERATED_H
