#include "TurnManager.h"

ATurnManager::ATurnManager()
{
    PrimaryActorTick.bCanEverTick = true;
}

void ATurnManager::BeginPlay()
{
    Super::BeginPlay();
}

void ATurnManager::Tick(float DeltaTime)
{
    Super::Tick(DeltaTime);
}
