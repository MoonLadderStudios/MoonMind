#pragma once

#include "CoreMinimal.h"
#include "TurnManager.h" // Assuming strategy manager might also be a specialized version or component of TurnManager
#include "TurnManager_Strategy.generated.h"

UCLASS()
class ATurnManager_Strategy : public ATurnManager // Or another appropriate base class like UObject if it's a component
{
    GENERATED_BODY()

public:
    ATurnManager_Strategy();

protected:
    // Using UObject as base doesn't have BeginPlay, if it's an Actor it would.
    // virtual void BeginPlay() override;

public:
    // Using UObject as base doesn't have Tick, if it's an Actor it would.
    // virtual void Tick(float DeltaTime) override;

    // Add strategy-specific functions and properties here
};
