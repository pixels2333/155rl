::manbow.CompileFile("data/script/background/bg08_base.nut", this);
local _base_update = ("Update" in this) ? this.Update : null;

this.mob_dir <- "data/background/bg08/mob/";
::manbow.CompileFile("data/script/background/mob.nut", this);

this.SetMob(this.actor0, "mob_inuA", "mob_inuA", "data/background/mob/");
this.SetMob(this.actor0, "mob_inuB", "mob_inuB", "data/background/mob/");
this.SetMob(this.actor0, "mob_inuC", "mob_inuC", "data/background/mob/");
this.SetMob(this.actor0, "mob_hyouA", "mob_hyouA", "data/background/mob/");
this.SetMob(this.actor0, "mob_orin", "mob_orin", "data/background/mob/");
this.SetMob(this.actor0, "mob_nekoA", "mob_nekoA", "data/background/mob/");
this.SetMob(this.actor0, "mob_nekoB", "mob_nekoB", "data/background/mob/");
this.SetMob(this.actor0, "mob_nekoC", "mob_nekoC", "data/background/mob/");
this.SetMob(this.actor0, "mob_tokage", "mob_tokage", "data/background/mob/");
this.SetMob(this.actor0, "mob_araiguma", "mob_araiguma", "data/background/mob/");

local t_paru = this.SetMob(this.actor0, "mob_paru", "mob_paru", "data/background/mob/");
t_paru.mat_local.SetScaling(1.0, 1.0, 1.0);

this.SetMob(this.actor0, "mob_hyouB", "mob_hyouB", "data/background/mob/");
this.SetMob(this.actor0, "mob_tori", "mob_tori", "data/background/mob/");

local dokuroA = this.SetMob(this.actor0, "mob_dokuroA", "mob_dokuroA", "data/background/mob/");
local dokuroB = this.SetMob(this.actor0, "mob_dokuroB", "mob_dokuroB", "data/background/mob/");

this.SetMob(this.actor0, "mob_satori", "mob_satori", "data/background/mob/");
this.SetMob(this.actor0, "mob_okuu", "mob_okuu", "data/background/mob/");

local CopyDokuro = function(name, target)
{
	local t = {};
	t.actor <- ::manbow.AnimationController2D();
	t.actor.Init(target.animation_set2d);
	t.actor.SetMotion(0, 0);
	t.matrix <- ::manbow.Matrix();
	this.actor0.GetMeshNodeMatrix("00" + name, t.matrix);
	t.mat_local <- ::manbow.Matrix();
	t.mat_local.SetIdentity();
	t.pos <- ::manbow.Vector3();
	t.pos.x = 0;
	t.pos.y = 0;
	t.pos.z = 0;
	t.SetWorldTransform <- target.SetWorldTransform;
	t.Update <- target.Update;
	t.actor.ConnectRenderSlot(::graphics.slot.background2d_0, 100);
	this.mob[name] <- t;
	this.actor_mob.append(t);
	return t;
};

for (local i = 1; i < 10; i++)
{
	CopyDokuro.call(this, "mob_dokuroA0" + i, dokuroA);
	CopyDokuro.call(this, "mob_dokuroB0" + i, dokuroB);
	this.SetUpdateFunctionType1("mob_dokuroA0" + i, 0.5 + 0.1 * i, 0.01, 35.0, 1234 * i);
	this.SetUpdateFunctionType1("mob_dokuroB0" + i, 0.5 + 0.1 * i, 0.01, 35.0, 123456 * i);
}

this.SetUpdateFunctionType1("mob_dokuroA", 0.5, 0.01, 35.0, 1234);
this.SetUpdateFunctionType1("mob_dokuroB", 0.5, 0.01, 35.0, 123456);

local t_yamame = this.SetMob(this.actor0, "mob_yamame", null, "data/background/mob/");
t_yamame.flag1 <- 0;
t_yamame.Update <- function()
{
	this.flag1++;
	this.mat_local.SetRotation(0, 0, 3 * sin(this.flag1 * 2 * 0.0174533) * 0.0174533);
};

local sort_func = function(a, b)
{
	local v0 = ::manbow.Vector3();
	local v1 = ::manbow.Vector3();
	v0.x = 0; v0.y = 0; v0.z = 0;
	v1.x = 0; v1.y = 0; v1.z = 0;
	a.matrix.Vec3Transform(v0);
	b.matrix.Vec3Transform(v1);
	if (v0.z < v1.z) return 1;
	if (v0.z > v1.z) return -1;
	return 0;
};
this.actor_mob.sort(sort_func);
foreach (a in this.actor_mob)
{
	a.actor.ConnectRenderSlot(::graphics.slot.background2d_0, 100);
}

this.Update <- function()
{
	if (_base_update)
	{
		_base_update.call(this);
	}
	this.UpdateMob();
};
