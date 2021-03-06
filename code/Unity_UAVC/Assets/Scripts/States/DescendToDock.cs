﻿internal class DescendToDock : IState
{
    private readonly Drone _drone;

    public DescendToDock(Drone drone)
    {
        _drone = drone;
    }

    public void Tick()
    {
        _drone.ConsumeBattery();
    }

    public void OnEnter()
    {
        _drone.GoToPos(_drone.dock.position);
    }

    public void OnExit()
    {
        _drone.Stop();
    }
}