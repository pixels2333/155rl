::manbow.CompileFile("data/script/background/bg10_base.nut", this);
local _base_update = ("Update" in this) ? this.Update : null;
::manbow.CompileFile("data/script/background/mob.nut", this);

this.SetMob(this.actor0, "mob_tanukiA");
this.SetMob(this.actor0, "mob_tanukiB");
this.SetMob(this.actor0, "mob_tanukiC");
this.SetMob(this.actor0, "mob_tanukiD");
this.SetMob(this.actor0, "mob_tanukiE");

local t_tanukiF = this.SetMob(this.actor0, "mob_tanukiF");
t_tanukiF.mat_local.SetScaling(0.8, 0.8, 1.0);

this.SetMob(this.actor0, "mob_tanukiG");
this.SetMob(this.actor0, "mob_tanukiH");
this.SetMob(this.actor0, "mob_tanukiI");
this.SetMob(this.actor0, "mob_tanukiJ");
this.SetMob(this.actor0, "mob_tanukiK");
this.SetMob(this.actor0, "mob_tanukiL");
this.SetMob(this.actor0, "mob_tanukiM");
this.SetMob(this.actor0, "mob_tanukiN");

local t_tanukiO = this.SetMob(this.actor0, "mob_tanukiO");
t_tanukiO.mat_local.SetScaling(0.8, 0.8, 1.0);

this.SetMob(this.actor0, "mob_tanukiP");

local t_tanukiQ = this.SetMob(this.actor0, "mob_tanukiQ");
t_tanukiQ.mat_local.SetScaling(0.8, 0.8, 1.0);

this.SetMob(this.actor0, "mob_tanukiR");

local t_tanukiS = this.SetMob(this.actor0, "mob_tanukiS");
t_tanukiS.mat_local.SetScaling(0.8, 0.8, 1.0);

this.SetMob(this.actor0, "mob_tanukiT");

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
