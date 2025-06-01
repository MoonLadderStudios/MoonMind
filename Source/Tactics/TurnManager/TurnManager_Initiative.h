#pragma once

#include "CoreMinimal.h"
#include "TurnManager.h" // Assuming initiative manager might be a specialized version of TurnManager or interact closely
#include "TurnManager_Initiative.generated.h"

UCLASS()
class ATurnManager_Initiative : public ATurnManager // Or another appropriate base class
{
    GENERATED_BODY()

public:
    ATurnManager_Initiative();

protected:
    virtual void BeginPlay() override;

public:
    virtual void Tick(float DeltaTime) override;

    // Add initiative-specific functions and properties here
};
