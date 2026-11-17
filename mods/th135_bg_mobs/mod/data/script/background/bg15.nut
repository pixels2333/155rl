::manbow.CompileFile("data/script/background/bg15_base.nut", this);
local _base_update = ("Update" in this) ? this.Update : null;
::manbow.CompileFile("data/script/background/mob.nut", this);
local _bound_cam3d = null;

this.actor_mob_3d <- [];
local SetMob3D = function(name_, pat_, wx, wy, wz)
{
	local t = {};
	t.animation_set2d <- ::manbow.AnimationSet2D();
	t.animation_set2d.Load("data/background/mob/" + pat_ + ".pat", null);
	t.actor <- ::manbow.AnimationController2D();
	t.actor.Init(t.animation_set2d);
	t.actor.SetMotion(0, 0);
	t.mat_node <- ::manbow.Matrix();
	t.mat_node.SetScaling(-1.0, 1.0, 1.0);
	t.mat_node.Rotate(3.14159265 / 2.0, 0.0, 0.0);
	t.mat_node.Translate(wx, wy, wz);
	t.node_name <- "00" + name_;
	t.Update <- function()
	{
	};
	t.actor.ConnectRenderSlot(::graphics.slot.background3d_mob, 100);
	this.actor_mob_3d.append(t);
	return t;
};

SetMob3D.call(this, "mob_suika", "mob_suika", -72.3755, -287.3781, -398.2827);
SetMob3D.call(this, "mob_yuugi", "mob_yuugi", 64.2913, -291.6811, -265.2639);

local t;
t = this.SetMob(this.actor0, "mob_kosuzu");
t.mat_local.SetScaling(0.75, 0.75, 1.0);
this.SetMob(this.actor0, "mob_A");
this.SetMob(this.actor0, "mob_kourin");
this.SetMob(this.actor0, "mob_arisu");
this.SetMob(this.actor0, "mob_E");
this.SetMob(this.actor0, "mob_D");
this.SetMob(this.actor0, "mob_C");
this.SetMob(this.actor0, "mob_B");
this.SetMob(this.actor0, "mob_G");
this.SetMob(this.actor0, "mob_F");
this.SetMob(this.actor0, "mob_H");
this.SetMob(this.actor0, "mob_I");
this.SetMob(this.actor0, "mob_eirin");
this.SetMob(this.actor0, "mob_tei");
this.SetMob(this.actor0, "mob_udon");
this.SetMob(this.actor0, "mob_kaguya");
this.SetMob(this.actor0, "mob_J");
this.SetMob(this.actor0, "mob_K");
this.SetMob(this.actor0, "mob_mokou");
this.SetMob(this.actor0, "mob_keine");

// TH155 bg02.msh stripped Root and all 20 00mob_* dummy nodes; reconstruct exact TH13.5 bg02.msh world matrices.
local _mat_root = ::manbow.Matrix();
_mat_root.SetScaling(0.9, 0.9, 0.9);
_mat_root.Translate(-264.585999, 0.0, -54.26413);

local _mat_actor0 = ::manbow.Matrix();
_mat_actor0.SetScaling(-1.0, 1.0, 1.0);
_mat_actor0.Rotate(3.14159265 / 2.0, 0.0, 0.0);

local _bg02_nodes = {
	mob_A = [-739.316895, 720.508545, -36.501854],
	mob_B = [-618.31, -505.69, -36.50],
	mob_C = [-544.89, -505.69, -36.50],
	mob_D = [-461.68, -397.59, -34.83],
	mob_E = [-349.89, -359.20, -34.83],
	mob_F = [-215.79, -413.65, -36.42],
	mob_G = [-104.56, -364.40, -36.42],
	mob_H = [42.12, -169.94, -30.52],
	mob_I = [137.70, -169.94, -30.52],
	mob_J = [663.69, -528.33, -36.48],
	mob_K = [-465.24, -505.69, -36.50],
	mob_kourin = [-468.71, 269.85, -36.50],
	mob_tei = [245.31, -385.42, 1.05],
	mob_eirin = [308.77, -387.63, -13.60],
	mob_kaguya = [379.36, -390.52, -12.11],
	mob_udon = [429.36, -391.44, -12.11],
	mob_mokou = [886.51, -490.65, -22.63],
	mob_keine = [960.93, -526.50, -22.63],
	mob_arisu = [1357.43, -272.92, -36.50],
	mob_kosuzu = [-1620.24, 1786.79, -36.50]
};

foreach (_k, _p in _bg02_nodes)
{
	local _m = this.mob[_k].matrix;
	_m.SetIdentity();
	_m.Translate(_p[0], _p[1], _p[2]);
	_m.Multiply(_mat_root);
	_m.Multiply(_mat_actor0);
}

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

	::camera.camera3d.GetBillboardMatrix(mat_world);
	mat_world.Scale(-1.0, 1.0, 1.0);
	mat_world.Rotate(3.14159 / 2.0, 0, 0);

	for (local i = 0; i < this.actor_mob_3d.len(); i++)
	{
		this.actor_mob_3d[i].Update();
		mat_tmp.Set(mat_world);
		mat_tmp.Scale(1.8, 1.8, 1.8);
		mat_tmp.Multiply(this.actor_mob_3d[i].mat_node);
		this.actor_mob_3d[i].actor.SetWorldTransform(mat_tmp);
		this.actor_mob_3d[i].actor.Update();
	}

	this.UpdateMob();
};
