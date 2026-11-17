::manbow.CompileFile("data/script/background/bg11_base.nut", this);
local _base_update = ("Update" in this) ? this.Update : null;
::manbow.CompileFile("data/script/background/mob.nut", this);

this.SetMob(this.actor0, "mob_A", "mob_A_k");
this.SetMob(this.actor0, "mob_B", "mob_B_k");
this.SetMob(this.actor0, "mob_C", "mob_C_k");
this.SetMob(this.actor0, "mob_D", "mob_D_k");
this.SetMob(this.actor0, "mob_E", "mob_E_k");
this.SetMob(this.actor0, "mob_F", "mob_F_k");
this.SetMob(this.actor0, "mob_G", "mob_G_k");
this.SetMob(this.actor0, "mob_H", "mob_H_k");
this.SetMob(this.actor0, "mob_I", "mob_I_k");
this.SetMob(this.actor0, "mob_J", "mob_J_k");
this.SetMob(this.actor0, "mob_K", "mob_K_k");

foreach (a in this.actor_mob)
{
	a.actor.green = a.actor.red = 0.4;
}

this.Update <- function()
{
	if (_base_update)
	{
		_base_update.call(this);
	}
	this.UpdateMob();
};
