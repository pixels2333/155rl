::manbow.CompileFile("data/script/background/bg03_base.nut", this);
local _base_update = ("Update" in this) ? this.Update : null;
::manbow.CompileFile("data/script/background/mob.nut", this);
local _bound_cam3d = null;

this.actor_mob_3d <- [];
local SetMob3D = function(name_, pat_)
{
	local t = {};
	t.animation_set2d <- ::manbow.AnimationSet2D();
	t.animation_set2d.Load("data/background/mob/" + pat_ + ".pat", null);
	t.actor <- ::manbow.AnimationController2D();
	t.actor.Init(t.animation_set2d);
	t.actor.SetMotion(0, 0);
	t.matrix <- ::manbow.Matrix();
	t.node_name <- "00" + name_;
	t.pos <- ::manbow.Vector3();
	t.pos.x = 0;
	t.pos.y = 0;
	t.pos.z = 0;
	t.Update <- function()
	{
	};
	t.actor.ConnectRenderSlot(::graphics.slot.background3d_mob, 100);
	this.actor_mob_3d.append(t);
	return t;
};

local t_nue = SetMob3D.call(this, "mob_nue", "mob_nue");
t_nue.flag1 <- 0;
t_nue.Update <- function()
{
	this.flag1++;
	this.pos.y = 20.0 * sin(this.flag1 * 3 * 0.0174533);
};

SetMob3D.call(this, "mob_nazu-rin", "mob_nazu-rin");
SetMob3D.call(this, "mob_murasa", "mob_murasa");

this.SetMob(this.actor0, "mob_lily", "mob_haneA");
this.SetMob(this.actor0, "mob_lily");
this.SetMob(this.actor0, "mob_meruran");
this.SetMob(this.actor0, "mob_ririka");
this.SetMob(this.actor0, "mob_runasa");
this.SetMob(this.actor0, "mob_toranpet");
this.SetMob(this.actor0, "mob_baiorin");
this.SetMob(this.actor0, "mob_tenko");
this.SetMob(this.actor0, "mob_iku");

this.SetUpdateFunctionType0("mob_lily", 1.0, 0.065, 5.0);
this.SetUpdateFunctionType0("mob_tenko", 1.0, 0.065, 10.0);
this.SetUpdateFunctionType0("mob_iku", 1.0, 0.091, 3.0);
this.SetUpdateFunctionType0("mob_meruran", 1.0, 0.081, 2.0);
this.SetUpdateFunctionType0("mob_ririka", 1.0, 0.071, 1.8);
this.SetUpdateFunctionType0("mob_runasa", 1.0, 0.061, 1.7);
this.SetUpdateFunctionType0("mob_toranpet", 1.0, 0.071, 1.8);
this.SetUpdateFunctionType0("mob_baiorin", 1.0, 0.088, 1.9);

this.Update <- function()
{
	if (_base_update)
	{
		_base_update.call(this);
	}

	if (::camera.camera3d != null && ::camera.camera3d != _bound_cam3d)
	{
		_bound_cam3d = ::camera.camera3d;
		_bound_cam3d.ConnectRenderSlot(::graphics.slot.background3d_mob, -1);
	}

	local mat_world = ::manbow.Matrix();
	local mat_tmp = ::manbow.Matrix();
	local mat_node = ::manbow.Matrix();
	local mat_view = ::manbow.Matrix();
	local mat_proj = ::manbow.Matrix();

	::camera.camera2d.GetViewProjectionMatrix(mat_proj);
	mat_proj.Inverse();
	::camera.camera3d.GetViewProjectionMatrix(mat_view);
	mat_view.Multiply(mat_proj);

	::camera.camera3d.GetBillboardMatrix(mat_world);
	mat_world.Scale(-1.0, 1.0, 1.0);
	mat_world.Rotate(3.14159 / 2.0, 0, 0);

	for (local i = 0; i < this.actor_mob_3d.len(); i++)
	{
		this.actor_mob_3d[i].Update();
		mat_tmp.Set(mat_world);
		this.actor1.GetTakeNodeMatrix(this.actor_mob_3d[i].node_name, mat_node);
		mat_tmp.Multiply(mat_node);
		mat_tmp.Translate(this.actor_mob_3d[i].pos.x, this.actor_mob_3d[i].pos.y, this.actor_mob_3d[i].pos.z);
		this.actor_mob_3d[i].actor.SetWorldTransform(mat_tmp);
		this.actor_mob_3d[i].actor.Update();
	}

	this.UpdateMob();
};
